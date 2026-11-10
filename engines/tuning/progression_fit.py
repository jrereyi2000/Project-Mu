"""
Offline progression fit (docs/done/MADDEN_PILOT_AND_TUNING_PLAN.md §2.4, S6).

  python3 -m engines.tuning.progression_fit <snapshot.json> [--seasons 5] [--budget 200] [--seed 0]
                                            [--out data/tuning/progression_fit.json] [--careers CSV] [--no-hold-shape]

Replays Project Mu's progression engine (engines/progression.py, its pure functions only: seed_player_ceiling,
compute_player_delta, upkeep_term, performance_term, simulated_performance, calibrate_league_term) over N seasons from a pc_save_reader snapshot league, scores the replayed league, and
searches the engine's module-level constants for a better score. It RECOMMENDS constants: it never writes
engines/progression.py (V2 core U7 owns that file). Patching is in memory, for one replay, and always restored.

replay(snapshot, seasons=5, params=None, seed=0) -> league history
  League: every player on a real team (TeamIndex 0-31; not Draft, FreeAgent or Retired). OVR level only (the ratings
  layer, apply_ratings_progression, isn't replayed: the fit's targets are about OVR and careers).
  Each season, in the engine's own order (progression.run_simulation):
  1. Games (inputs the engine reads from the season just played), with seeded noise:
     - role: a team's best STARTER_SLOTS players per position group (by OVR) start;
     - games: starters get the snapshot's mean starter games of its last season, backups the backup mean (from
       GAMESPLAYED in season_stats; STARTER_GAMES / BACKUP_GAMES when the save has under ROLE_MIN_SAMPLE lines);
       in season 1 a player with his own last-season line keeps his own games; then +-1 noise;
     - missed time: with the snapshot's injured share (INJURY_SHARE when it has none) a player misses 1..10
       weeks; 8+ is IR.
  2. Performance: season 1 ranks the snapshot's last-season production (progression.production on the season's
     lines, plus seeded noise) inside each position group where the save has at least PROD_MIN_PLAYERS distinct
     lines; everyone else, and every later season, gets progression.simulated_performance (OVR plus noise, with
     All-Pro bonuses).
  3. Exits (simple, documented here, not part of the engine). Everyone older than RETIRE_AGE (RETIRE_AGE_KP for K
     and P) retires. The rest leave by a seeded draw on a hazard of age and OVR:
       h = EXIT_BASE + EXIT_PER_YEAR x max(0, age - EXIT_AGE) + CUT_HAZARD if he has 1+ years pro and an OVR below
           his position group's CUT_QUANTILE at the start (the snapshot; a release),
     scaled by one factor a season so the expected exits equal the snapshot's rookie class size (the save's own
     yearly inflow; at least the forced retirements). So how many leave is Madden's, and who leaves (old, or
     overtaken) follows the progression.
  4. Progression for everyone who stays: age + 1 (as run_simulation), compute_player_delta with the games, IR and
     missed weeks above, performance_term and upkeep_term, and the league-shape terms (calibrate_league_term on a
     stratified sample of at most CALIBRATION_SAMPLE players, its target scaled to the sample) holding the start's
     90+ count and mean OVR, as the engine does (hold_shape; off with hold_shape=False). Dev traits keep their
     labels, as run_simulation (the engine has no dev lottery since the receipt-driven progression). Years pro + 1.
  5. Rookies: each leaver is replaced by a rookie of his position group on his team, cloned from the snapshot's own
     rookie class (players with years_pro 0: OVR +-1, position, age, dev), so the league keeps its size and mix.

fit(snapshot, budget=200, seed=0) -> {params, score_before, score_after, detail, ...}
  A coordinate search over TUNABLES (each constant or tuple element with a step and bounds): try +step then -step,
  keep a change only when the score drops; after a pass with no gain every step halves (integer steps stay >= 1);
  stops when the budget (replays) is spent or no step is left. Score (lower is better, 0 = on every target), the
  mean of two halves:
  - league shape: each replayed season against the snapshot's start, per position group (QB RB REC OL FRONT DB K P,
    groups with 10+ players): OVR p10 / p50 / p90 (tolerance SHAPE_TOL_OVR), the 90+ count (10% of the start, at
    least 2) and mean age (0.5 years); error = |sim - start| / tolerance, averaged;
  - career arcs, from data/history/nfl_careers_through_2025.csv (career totals, first and last season). The file is
    a cross-section: 1,713 of its 1,886 careers are still going in 2025 (the players of the 2026 rosters), so year-
    to-year retention read from it is survivor-biased (96-98%) and isn't scored. Both targets use the players active
    in its last season, by years pro (k = last season - first season; age ~ 22 + k), bands YEARS_BANDS:
      years_share  career length so far: each band's share of the active players (tolerance YEARS_TOL); the
                   replay's is each season's league after the draft
      games_rel    games in the k-th year, as the difference of mean career games between active careers of k+1 and
                   k seasons (C(k+1) - C(k)), relative to the best band (the decay shape, not the level: the CSV only
                   has players with stat lines); tolerance GAMES_TOL. The replay's value is the mean games of players
                   who played, by years pro, relative to its best band.
Writes data/tuning/progression_fit.json (params, score before and after, per-target detail, the search history) and
data/tuning/progression_fit.md (the report). Cost: one replay is about 0.8 s a season on a full league with the
league term on, so --budget 200 --seasons 5 runs about 15 minutes.
"""
import argparse
import contextlib
import csv
import datetime as dt
import json
import math
import os
import random
import re
import sys
import time
from collections import Counter, defaultdict

from engines import progression as P
from engines.trade_value import fit_aging
from engines.tuning import compare as C
from engines.tuning import targets as T
from pipeline.load_save import DEV_TRAITS, POSITIONS

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT_DIR = os.path.join(ROOT, "data", "tuning")
DEFAULT_OUT = os.path.join(OUT_DIR, "progression_fit.json")
CAREERS_CSV = os.path.join(ROOT, "data", "history", "nfl_careers_through_2025.csv")

REAL_TEAMS = 32
NOT_ROSTERED = ("Draft", "FreeAgent", "Retired")
SEASON_GAMES = 17

# roles and games
STARTER_SLOTS = {"QB": 1, "RB": 1, "REC": 4, "OL": 5, "FRONT": 7, "DB": 4, "K": 1, "P": 1}
STARTER_GAMES, BACKUP_GAMES = 16.0, 8.0     # used when the snapshot has under ROLE_MIN_SAMPLE lines for a role
ROLE_MIN_SAMPLE = 30
INJURY_SHARE = 0.11                         # used when the snapshot has no injured player
MISSED_WEEKS = (1, 10)
IR_WEEKS = 8
PROD_MIN_PLAYERS = 8

# exits (this module's rules, not the engine's)
RETIRE_AGE, RETIRE_AGE_KP = 37, 41
CUT_QUANTILE, CUT_HAZARD = 0.10, 0.6
EXIT_BASE, EXIT_PER_YEAR, EXIT_AGE = 0.05, 0.06, 29

CALIBRATION_SAMPLE = 400

# scoring
SHAPE_GROUPS = ("QB", "RB", "REC", "OL", "FRONT", "DB", "K", "P")
SHAPE_MIN_PLAYERS = 10
SHAPE_TOL_OVR = 2.0
COUNT90_TOL_FRAC, COUNT90_TOL_MIN = 0.10, 2.0
AGE_TOL = 0.5
YEARS_BANDS = ((0, 0, "0"), (1, 2, "1-2"), (3, 5, "3-5"), (6, 99, "6+"))
YEARS_TOL = 0.03
GAMES_TOL = 0.10
SHAPE_WEIGHT, CAREER_WEIGHT = 0.5, 0.5

# The constants the fit may move: (name, step, lo, hi). Names are engines/progression.py module constants, with
# [i] / [i][j] for tuple elements. Integer constants keep integer values.
TUNABLES = (
    ("AGE_BAND_OFFSET[0]", 0.2, -2.0, 1.0),
    ("AGE_BAND_OFFSET[1]", 0.2, -2.0, 1.0),
    ("AGE_BAND_OFFSET[2]", 0.2, -2.0, 1.0),
    ("AGE_BAND_OFFSET[3]", 0.2, -2.0, 1.0),
    ("AGE_BAND_OFFSET[4]", 0.2, -2.0, 1.0),
    ("AGE_BAND_OFFSET[5]", 0.2, -2.0, 1.0),
    ("PERF_WEIGHT", 0.25, 0.0, 2.0),
    ("UPKEEP_K", 0.1, 0.0, 1.5),
    ("BAND_GROWTH_PER_SPREAD[0][1]", 0.1, 0.0, 1.0),
    ("BAND_GROWTH_PER_SPREAD[1][1]", 0.1, 0.0, 1.0),
    ("BAND_GROWTH_PER_SPREAD[2][1]", 0.1, 0.0, 1.0),
    ("BAND_GROWTH_PER_SPREAD[3][1]", 0.1, 0.0, 1.0),
    ("BAND_ROOM_FULL", 1.0, 1.0, 8.0),
    ("LEAGUE_TERM_WEIGHT[0][1]", 0.1, 0.0, 1.5),
    ("LEAGUE_TERM_WEIGHT[1][1]", 0.1, 0.0, 1.5),
    ("LEAGUE_TERM_WEIGHT[2][1]", 0.1, 0.0, 1.5),
    ("LEAGUE_TERM_OVR_WEIGHT[0][1]", 0.1, 0.0, 1.5),
    ("LEAGUE_TERM_OVR_WEIGHT[1][1]", 0.1, 0.0, 1.5),
    ("LEAGUE_TERM_OVR_WEIGHT[2][1]", 0.1, 0.0, 1.5),
    ("STAR_OVR", 1, 82, 92),
    ("STAR_MAX_AGE", 1, 28, 35),
    ("MAX_SEASON_CHANGE", 1, 4, 10),
)


# ---------- params: read and patch engines/progression.py constants (in memory only) ----------

_KEY_RE = re.compile(r"^([A-Z][A-Z0-9_]*)((?:\[\d+\])*)$")


def _parse_key(key):
    m = _KEY_RE.match(key)
    if not m:
        raise KeyError(f"not a progression constant: {key!r}")
    name, idx = m.group(1), [int(i) for i in re.findall(r"\[(\d+)\]", m.group(2))]
    if not hasattr(P, name) or callable(getattr(P, name)):
        raise KeyError(f"engines.progression has no constant {name}")
    return name, idx


def get_param(key):
    name, idx = _parse_key(key)
    v = getattr(P, name)
    for i in idx:
        v = v[i]
    return v


def _with(container, idx, value):
    if not idx:
        return value
    items = list(container)
    items[idx[0]] = _with(items[idx[0]], idx[1:], value)
    return type(container)(items) if isinstance(container, tuple) else items


def _typed(old, value):
    if isinstance(old, bool):
        return bool(value)
    if isinstance(old, int):
        return int(round(value))
    return float(value) if isinstance(old, (int, float)) else value


@contextlib.contextmanager
def patched(params=None):
    """Sets engines/progression.py constants for the block ({"PERF_WEIGHT": 0.8, "AGE_BAND_OFFSET[3]": -0.1}) and
    restores every original value afterwards, even on an error. Never writes the module file."""
    originals = {}
    try:
        for key, value in (params or {}).items():
            name, idx = _parse_key(key)
            if name not in originals:
                originals[name] = getattr(P, name)
            current = getattr(P, name)
            old = current
            for i in idx:
                old = old[i]
            setattr(P, name, _with(current, idx, _typed(old, value)))
        yield
    finally:
        for name, value in originals.items():
            setattr(P, name, value)


# ---------- the snapshot league ----------

def perf_group(position):
    return next((g for g, members in P.PERF_GROUPS.items() if position in members), None)


def _quantile(values, q):
    return T.percentile(values, q * 100)


def _last_year(snapshot):
    """The franchise year whose season lines feed season 1: the latest year with lines, not the current one while
    its regular season is still being played."""
    s = snapshot.get("season", {})
    counts = Counter()
    for p in snapshot.get("players", []):
        for y in {r.get("stats", {}).get("SEAS_YEAR") for r in p.get("season_stats") or []}:
            if y is not None:
                counts[y] += 1
    years = sorted((y for y, n in counts.items() if n >= 1), reverse=True)
    current = s.get("franchise_year")
    in_progress = s.get("week_type") in ("PreSeason", "RegularSeason")
    for y in years:
        if y == current and in_progress:
            continue
        return y
    return None


def _production(group, rows):
    line = C._player_line(rows)
    line["games_played"] = max((r.get("stats", {}).get("GAMESPLAYED") or 0) for r in rows)
    line = T._with_rates(line)
    return P.production(group, line)


def league_from_snapshot(snapshot):
    """The replay's starting league from a pc_save_reader snapshot (see the module docstring)."""
    year = _last_year(snapshot)
    players, injured = [], 0
    for p in snapshot.get("players", []):
        team = p.get("team_index")
        if team is None or team >= REAL_TEAMS or p.get("status") in NOT_ROSTERED or p.get("overall") is None:
            continue
        pos = p.get("position")
        rows = [r for r in p.get("season_stats") or [] if r.get("stats", {}).get("SEAS_YEAR") == year]
        group = perf_group(pos) or "OTHER"
        games = max((r.get("stats", {}).get("GAMESPLAYED") or 0) for r in rows) if rows else None
        prod = _production(group, rows) if rows and group != "OTHER" else None
        injured += (p.get("injury") or {}).get("status") == "Injured"
        players.append({"pid": int(p["pgid"]), "team": team, "position": pos, "engine_position": POSITIONS.get(pos, pos),
                        "group": group, "age": int(p.get("age") or 25), "ovr": int(p["overall"]),
                        "dev": DEV_TRAITS.get(p.get("dev_trait") or "Normal", "Normal"),
                        "years_pro": int(p.get("years_pro") or 0), "last_games": games, "last_prod": prod})
    players.sort(key=lambda x: x["pid"])
    starters = _starters(players)
    role = {"starter": [], "backup": []}
    for p in players:
        if p["last_games"] is not None:
            role["starter" if starters[p["pid"]] else "backup"].append(p["last_games"])
    games = {"starter": sum(role["starter"]) / len(role["starter"]) if len(role["starter"]) >= ROLE_MIN_SAMPLE
             else STARTER_GAMES,
             "backup": sum(role["backup"]) / len(role["backup"]) if len(role["backup"]) >= ROLE_MIN_SAMPLE
             else BACKUP_GAMES}
    by_group = defaultdict(list)
    for p in players:
        by_group[p["group"]].append(p["ovr"])
    rookies = [{k: p[k] for k in ("position", "engine_position", "group", "age", "ovr", "dev")}
               for p in players if p["years_pro"] == 0]
    s = snapshot.get("season", {})
    return {"_league": True, "players": players, "rookies": rookies,
            "calendar_year": s.get("calendar_year") or 0, "last_year": year,
            "injury_share": injured / len(players) if injured and players else INJURY_SHARE,
            "games": {k: round(v, 2) for k, v in games.items()},
            "cut_line": {g: _quantile(v, CUT_QUANTILE) for g, v in by_group.items()},
            "shape": league_shape(players)}


def _starters(players):
    by = defaultdict(list)
    for p in players:
        by[(p["team"], p["group"])].append(p)
    out = {}
    for (_, group), ps in by.items():
        n = STARTER_SLOTS.get(group, 0)
        for i, p in enumerate(sorted(ps, key=lambda p: (-p["ovr"], p["pid"]))):
            out[p["pid"]] = i < n
    return out


def league_shape(players):
    n = len(players)
    by_group = defaultdict(list)
    for p in players:
        by_group[p["group"]].append(p["ovr"])
    groups = {}
    for g in SHAPE_GROUPS:
        vals = by_group.get(g, [])
        if vals:
            groups[g] = {"n": len(vals), "p10": round(_quantile(vals, 0.1), 2), "p50": round(_quantile(vals, 0.5), 2),
                         "p90": round(_quantile(vals, 0.9), 2), "mean": round(sum(vals) / len(vals), 2)}
    years = Counter(years_band(p["years_pro"]) for p in players)
    return {"players": n, "count_90_plus": sum(1 for p in players if p["ovr"] >= 90),
            "avg_ovr": round(sum(p["ovr"] for p in players) / n, 2) if n else 0.0,
            "avg_age": round(sum(p["age"] for p in players) / n, 2) if n else 0.0, "groups": groups,
            "years_share": {label: round(years[label] / n, 4) for _, _, label in YEARS_BANDS} if n else {}}


def years_band(years_pro):
    return next(label for lo, hi, label in YEARS_BANDS if lo <= (years_pro or 0) <= hi)


# ---------- replay ----------

def _performance(roster, league, yr, sim_s, seed):
    """{pid: {score, awards, group}} for the season just played."""
    perf_season = sim_s - 1 + 7919 * seed
    view = [{"player_id": p["pid"], "position": p["position"], "ovr": p["ovr"]} for p in roster]
    perf = P.simulated_performance(view, perf_season)
    if yr == 1:
        rng = random.Random(f"fit-prod:{seed}")
        by_group = defaultdict(list)
        for p in roster:
            if p.get("last_prod") is not None:
                by_group[p["group"]].append(p)
        for group, ps in by_group.items():
            values = [p["last_prod"] for p in ps]
            if len(set(values)) < PROD_MIN_PLAYERS:
                continue
            sd = (sum((v - sum(values) / len(values)) ** 2 for v in values) / len(values)) ** 0.5 or 1.0
            ranked = sorted(ps, key=lambda p: (p["last_prod"] + rng.gauss(0.0, 0.1 * sd), p["pid"]))
            n = len(ranked)
            for i, p in enumerate(ranked):
                perf.setdefault(p["pid"], {"awards": 0.0, "notes": []})["score"] = round(2 * (i / (n - 1)) - 1, 3)
    for p in roster:
        if p["pid"] in perf:
            perf[p["pid"]]["group"] = p["group"]
    return perf


def replay(snapshot, seasons=5, params=None, seed=0, hold_shape=True):
    """Replays engines/progression.py over `seasons` seasons from a snapshot (or a league_from_snapshot result).
    Deterministic for a seed. Returns {"start": shape, "seasons": [per-season shape, exits, retention and games by
    years pro, league terms], ...}."""
    league = snapshot if snapshot.get("_league") else league_from_snapshot(snapshot)
    with patched(params):
        return _replay(league, seasons, seed, hold_shape)


def _replay(league, seasons, seed, hold_shape):
    aging = fit_aging([])
    roster = [dict(p) for p in league["players"]]
    base = league["shape"]
    size = len(roster)
    rookie_pool = defaultdict(list)
    for r in league["rookies"]:
        rookie_pool[r["group"]].append(r)
    all_rookies = league["rookies"] or [{k: p[k] for k in ("position", "engine_position", "group", "age", "ovr",
                                                           "dev")} for p in roster]
    next_pid = 10_000_000 + max([p["pid"] for p in roster] + [0])
    history = []
    for yr in range(1, seasons + 1):
        sim_s = league["calendar_year"] + yr
        rng = random.Random(f"fit:{seed}:{sim_s}")
        starters = _starters(roster)
        perf = _performance(roster, league, yr, sim_s, seed)

        # 1. games
        games_by_band = defaultdict(lambda: [0, 0])
        inputs = {}
        for p in roster:
            starter = starters[p["pid"]]
            base_games = league["games"]["starter" if starter else "backup"]
            if yr == 1 and p.get("last_games") is not None:
                base_games = p["last_games"]
            base_games = max(0.0, min(SEASON_GAMES, base_games + rng.choice((-1, 0, 0, 1))))
            missed = rng.randint(*MISSED_WEEKS) if rng.random() < league["injury_share"] else 0
            games = int(round(max(0.0, base_games - missed)))
            if games > 0:
                b = games_by_band[years_band(p["years_pro"])]
                b[0] += games
                b[1] += 1
            inputs[p["pid"]] = {"starter": starter, "games": games, "missed": missed, "ir": missed >= IR_WEEKS,
                                "games_missed": (SEASON_GAMES - games) if starter else missed}

        # 3. exits
        hazards = {p["pid"]: exit_hazard(p, league) for p in roster}
        forced = sum(1 for h in hazards.values() if h is None)
        factor = _exit_factor([h for h in hazards.values() if h is not None],
                              max(0, min(len(league["rookies"]), size) - forced))
        staying, leaving = [], []
        retention = defaultdict(lambda: [0, 0])
        for p in roster:
            band = retention[years_band(p["years_pro"])]
            band[1] += 1
            h = hazards[p["pid"]]
            gone = h is None or rng.random() < min(1.0, factor * h)
            (leaving if gone else staying).append(p)
            band[0] += not gone

        # 5. the rookie class, one per leaver (drawn before progression so the league term sees it, as the engine)
        rookies = []
        for p in leaving:
            src = rng.choice(rookie_pool.get(p["group"]) or all_rookies)
            next_pid += 1
            rookies.append({**src, "pid": next_pid, "team": p["team"], "years_pro": 0,
                            "ovr": max(P.OVR_MIN, min(P.OVR_MAX, src["ovr"] + rng.choice((-1, 0, 0, 1)))),
                            "last_games": None, "last_prod": None})

        # 4. progression
        prepared = {}
        for p in staying:
            i = inputs[p["pid"]]
            ceiling, _ = P.seed_player_ceiling(p["pid"], p["ovr"], p["age"] + 1, p["dev"])
            pf = perf.get(p["pid"])
            upkeep = P.upkeep_term(p["ovr"], (pf or {}).get("score"), i["games_missed"]) if hold_shape else 0.0
            prepared[p["pid"]] = (ceiling, P.performance_term(pf), upkeep, i)

        def progress(p, dev, star=0.0):
            ceiling, perf_t, upkeep, i = prepared[p["pid"]]
            return P.compute_player_delta(
                player_id=p["pid"], position=p["engine_position"], age=p["age"] + 1, ovr=p["ovr"], dev_trait=p["dev"],
                ceiling=ceiling, games_played=i["games"], downs_played=None, is_starter=i["starter"],
                is_ir=i["ir"], injury_weeks=i["missed"], season=sim_s, aging_curves=aging, league_adj=dev,
                star_adj=star, performance=perf_t, upkeep=upkeep)[1]

        terms = (0.0, 0.0)
        if hold_shape and staying:
            k = max(1, math.ceil(len(staying) / CALIBRATION_SAMPLE))      # one stride for both, so the mix holds
            sample, rookie_sample = _stratified(staying, k), _stratified(rookies, k)
            frac = (len(sample) + len(rookie_sample)) / max(1, len(staying) + len(rookies))
            target = {"count_90_plus": base["count_90_plus"] * frac, "avg_ovr": base["avg_ovr"]}
            terms = P.calibrate_league_term(sample, [r["ovr"] for r in rookie_sample], target, progress)[0]
        advanced = []
        for p in staying:
            new_ovr = progress(p, *terms)
            advanced.append({**p, "age": p["age"] + 1, "years_pro": p["years_pro"] + 1, "ovr": new_ovr,
                             "last_games": None, "last_prod": None})
        roster = sorted(advanced + rookies, key=lambda p: p["pid"])

        history.append({"season": sim_s, "shape": league_shape(roster), "exits": len(leaving),
                        "rookies": len(rookies), "league_term": terms[0], "star_term": terms[1],
                        "retention": {k: list(v) for k, v in sorted(retention.items())},
                        "games": {k: list(v) for k, v in sorted(games_by_band.items())}})
        assert len(roster) == size
    return {"start": base, "seasons": history, "players": size, "calendar_year": league["calendar_year"],
            "seed": seed, "hold_shape": hold_shape, "role_games": league["games"],
            "injury_share": round(league["injury_share"], 4), "last_year": league["last_year"]}


def _stratified(players, k):
    """Every k-th player by OVR (the best first), so the sample keeps the OVR spread."""
    return sorted(players, key=lambda p: (-p["ovr"], p["pid"]))[::k]


def exit_hazard(p, league):
    """None: retires for certain (age). Else the unscaled hazard of leaving after this season."""
    if p["age"] > (RETIRE_AGE_KP if p["group"] in ("K", "P") else RETIRE_AGE):
        return None
    cut = league["cut_line"].get(p["group"])
    released = p["years_pro"] >= 1 and cut is not None and p["ovr"] < cut
    return EXIT_BASE + EXIT_PER_YEAR * max(0, p["age"] - EXIT_AGE) + (CUT_HAZARD if released else 0.0)


def _exit_factor(hazards, need):
    """The factor f with sum(min(1, f h)) = need (bisection); 0 when nothing more needs to leave."""
    if need <= 0 or not hazards:
        return 0.0
    if need >= len(hazards):
        return float("inf")
    lo, hi = 0.0, 1.0
    while sum(min(1.0, hi * h) for h in hazards) < need:
        hi *= 2
    for _ in range(40):
        mid = (lo + hi) / 2
        if sum(min(1.0, mid * h) for h in hazards) < need:
            lo = mid
        else:
            hi = mid
    return hi


# ---------- targets ----------

def career_targets(path=CAREERS_CSV):
    """{"years_share": {band: share}, "games_rel": {band: share of the best band}, "games": {band: games},
    "retention_biased": {band: share} (reported only), "meta"} from the career-totals CSV (module docstring)."""
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    careers = []
    for r in rows:
        try:
            first, last = int(r["first_season"]), int(r["last_season"])
        except (KeyError, ValueError):
            continue
        careers.append((first, last - first + 1, float(r.get("games_played") or 0)))
    last_season = max(c[0] + c[1] - 1 for c in careers)
    active = [c for c in careers if c[0] + c[1] - 1 == last_season]
    years = Counter(years_band(span - 1) for _, span, _ in active)
    years_share = {label: round(years[label] / len(active), 4) for _, _, label in YEARS_BANDS} if active else {}
    retention = {}
    for lo, hi, label in YEARS_BANDS:
        num = den = 0
        for k in range(lo, min(hi, 20) + 1):
            for first, span, _ in careers:
                if first + k + 1 <= last_season and span >= k + 1:
                    den += 1
                    num += span >= k + 2
        if den:
            retention[label] = round(num / den, 4)
    by_span = defaultdict(list)
    for _, span, games in active:
        by_span[span].append(games)
    mean_c = {s: sum(v) / len(v) for s, v in by_span.items()}
    mean_c[0] = 0.0
    marginal = {k: mean_c[k + 1] - mean_c[k] for k in range(0, max(by_span)) if k + 1 in mean_c and k in mean_c}
    games = {}
    for lo, hi, label in YEARS_BANDS:
        vals = [v for k, v in marginal.items() if lo <= k <= hi]
        if vals:
            games[label] = round(sum(vals) / len(vals), 3)
    top = max(games.values()) if games else 1.0
    return {"years_share": years_share, "games": games,
            "games_rel": {k: round(v / top, 4) for k, v in games.items()} if top else {},
            "retention_biased": retention,
            "meta": {"source": os.path.relpath(path, ROOT) if os.path.isabs(path) else path,
                     "careers": len(careers), "active": len(active), "last_season": last_season}}


def _rel(d):
    top = max(d.values()) if d else 0
    return {k: v / top for k, v in d.items()} if top else {}


def score(history, careers):
    """(score, detail): lower is better; detail is [{key, sim, target, tol, error}] (shape errors averaged over the
    replayed seasons)."""
    start = history["start"]
    shape_errs = defaultdict(list)
    shape_sims = defaultdict(list)
    for s in history["seasons"]:
        sh = s["shape"]
        items = [("shape.count_90_plus", sh["count_90_plus"], start["count_90_plus"],
                  max(COUNT90_TOL_FRAC * start["count_90_plus"], COUNT90_TOL_MIN)),
                 ("shape.avg_age", sh["avg_age"], start["avg_age"], AGE_TOL)]
        for g, st in start["groups"].items():
            if st["n"] < SHAPE_MIN_PLAYERS or g not in sh["groups"]:
                continue
            for q in ("p10", "p50", "p90"):
                items.append((f"shape.{g}.{q}", sh["groups"][g][q], st[q], SHAPE_TOL_OVR))
        for key, sim, target, tol in items:
            shape_errs[key].append((abs(sim - target) / tol, target, tol))
            shape_sims[key].append(sim)
    detail = []
    for key, errs in shape_errs.items():
        detail.append({"key": key, "sim": round(sum(shape_sims[key]) / len(shape_sims[key]), 3),
                       "target": errs[0][1], "tol": errs[0][2],
                       "error": round(sum(e[0] for e in errs) / len(errs), 4)})
    games = defaultdict(lambda: [0, 0])
    for s in history["seasons"]:
        for k, (a, b) in s["games"].items():
            games[k][0] += a
            games[k][1] += b
    n_seasons = max(1, len(history["seasons"]))
    sim_years = {label: sum(s["shape"]["years_share"].get(label, 0.0) for s in history["seasons"]) / n_seasons
                 for _, _, label in YEARS_BANDS}
    sim_games = _rel({k: a / b for k, (a, b) in games.items() if b})
    career = []
    for label, target in careers.get("years_share", {}).items():
        if history["seasons"]:
            career.append({"key": f"career.years_share.{label}", "sim": round(sim_years[label], 4),
                           "target": target, "tol": YEARS_TOL,
                           "error": round(abs(sim_years[label] - target) / YEARS_TOL, 4)})
    for label, target in careers.get("games_rel", {}).items():
        if label in sim_games:
            career.append({"key": f"career.games_rel.{label}", "sim": round(sim_games[label], 4), "target": target,
                           "tol": GAMES_TOL, "error": round(abs(sim_games[label] - target) / GAMES_TOL, 4)})
    parts = []
    if detail:
        parts.append((SHAPE_WEIGHT, sum(d["error"] for d in detail) / len(detail)))
    if career:
        parts.append((CAREER_WEIGHT, sum(d["error"] for d in career) / len(career)))
    total = sum(w * v for w, v in parts) / sum(w for w, _ in parts) if parts else 0.0
    return round(total, 4), sorted(detail, key=lambda d: d["key"]) + career


# ---------- fit ----------

def _clamp(value, lo, hi, integer):
    value = max(lo, min(hi, value))
    return int(round(value)) if integer else round(value, 4)


def fit(snapshot, budget=200, seed=0, seasons=5, careers=None, tunables=None, start=None, hold_shape=True, log=None):
    """Coordinate search over `tunables` (default TUNABLES) from `start` (default: the engine's current constants).
    `budget` counts replays. Returns {params (recommended values that differ from the engine's), score_before,
    score_after, detail_before, detail_after, recommended, history, ...}."""
    log = log or (lambda *_: None)
    league = snapshot if snapshot.get("_league") else league_from_snapshot(snapshot)
    careers = careers if careers is not None else career_targets()
    if tunables is None:
        spec = list(TUNABLES)
    else:   # names from TUNABLES, or (name, step, lo, hi) tuples
        known = {t[0]: t for t in TUNABLES}
        spec = [known[t] if isinstance(t, str) else tuple(t) for t in tunables]
    engine = {name: get_param(name) for name, *_ in spec}
    current = {**engine, **(start or {})}
    steps = {name: step for name, step, *_ in spec}
    integer = {name: isinstance(engine[name], int) and not isinstance(engine[name], bool) for name, *_ in spec}
    bounds = {name: (lo, hi) for name, _, lo, hi in spec}
    evals = 0
    history = []

    def evaluate(params):
        nonlocal evals
        evals += 1
        h = replay(league, seasons, params, seed, hold_shape)
        return score(h, careers) + (h,)

    t0 = time.time()
    best, detail_before, h_before = evaluate(current)
    start_score = best
    log(f"start score {best:.4f} ({time.time() - t0:.1f}s a replay)")
    gains = defaultdict(float)
    while evals < budget:
        improved = False
        for name, *_ in spec:
            for sign in (1, -1):
                if evals >= budget:
                    break
                cand = _clamp(current[name] + sign * steps[name], *bounds[name], integer[name])
                if cand == current[name]:
                    continue
                s, _, _ = evaluate({**current, name: cand})
                history.append({"eval": evals, "param": name, "value": cand, "score": s, "accepted": s < best - 1e-9})
                if s < best - 1e-9:
                    log(f"[{evals}/{budget}] {name} {current[name]} -> {cand}: {best:.4f} -> {s:.4f}")
                    gains[name] += best - s
                    best, current[name], improved = s, cand, True
                    break
        if not improved:
            shrunk = False
            for name, step, *_ in spec:
                new = max(1, steps[name] // 2) if integer[name] else steps[name] / 2
                if new != steps[name] and (integer[name] or new >= step / 16):
                    steps[name], shrunk = new, True
            if not shrunk:
                break
    h_after = replay(league, seasons, current, seed, hold_shape)
    final_score, detail_after = score(h_after, careers)
    recommended = [{"param": name, "current": engine[name], "recommended": current[name],
                    "delta": round(current[name] - engine[name], 4), "gain": round(gains[name], 4)}
                   for name, *_ in spec if current[name] != engine[name]]
    recommended.sort(key=lambda r: (-r["gain"], r["param"]))
    return {"params": {r["param"]: r["recommended"] for r in recommended},
            "engine_params": engine, "start_params": dict(start or {}),
            "score_before": start_score, "score_after": final_score,
            "detail_before": detail_before, "detail_after": detail_after,
            "recommended": recommended, "evaluations": evals, "budget": budget, "seasons": seasons, "seed": seed,
            "hold_shape": hold_shape, "history": history, "careers": careers,
            "league": {"players": len(league["players"]), "rookies": len(league["rookies"]),
                       "calendar_year": league["calendar_year"], "last_year": league["last_year"],
                       "role_games": league["games"], "injury_share": round(league["injury_share"], 4),
                       "start_shape": league["shape"]},
            "replay_after": h_after["seasons"]}


# ---------- output ----------

def format_report(result, snapshot_path=None):
    lg = result["league"]
    lines = ["# Progression fit (recommendation only)", "",
             f"- Snapshot: `{snapshot_path or '-'}` ({lg['players']} rostered players, {lg['rookies']} rookies, "
             f"calendar year {lg['calendar_year']}; season-1 inputs from franchise year {lg['last_year']})",
             f"- Replay: {result['seasons']} seasons, seed {result['seed']}, league term "
             f"{'on' if result['hold_shape'] else 'off'}; role games {lg['role_games']}, injured share "
             f"{lg['injury_share']}",
             f"- Search: {result['evaluations']} of {result['budget']} replays",
             f"- **Score: {result['score_before']:.4f} before -> {result['score_after']:.4f} after** "
             "(0 = on every target; 1 = one tolerance off on average)", "",
             "These constants are a recommendation. Nothing was written to `engines/progression.py`; applying them is a "
             "follow-up for the progression owner (V2 core U7).", "",
             "## Recommended constants", ""]
    if result["recommended"]:
        lines += ["| constant | engine now | recommended | change | score gain |", "|---|---|---|---|---|"]
        lines += [f"| `{r['param']}` | {r['current']} | {r['recommended']} | {r['delta']:+g} | {r['gain']:.4f} |"
                  for r in result["recommended"]]
    else:
        lines.append("No change beat the engine's current constants within the budget.")
    before = {d["key"]: d for d in result["detail_before"]}
    lines += ["", "## Targets", "", "| target | goal | tol | before | after | error before | error after |",
              "|---|---|---|---|---|---|---|"]
    for d in result["detail_after"]:
        b = before.get(d["key"], {})
        lines.append(f"| `{d['key']}` | {d['target']} | {d['tol']:g} | {b.get('sim', '-')} | {d['sim']} | "
                     f"{b.get('error', 0):.2f} | {d['error']:.2f} |")
    lines += ["", "## Replayed league with the recommended constants", "",
              "| season | 90+ | mean OVR | mean age | exits | league term | star term |", "|---|---|---|---|---|---|---|",
              f"| start | {lg['start_shape']['count_90_plus']} | {lg['start_shape']['avg_ovr']} | "
              f"{lg['start_shape']['avg_age']} | - | - | - |"]
    for s in result["replay_after"]:
        sh = s["shape"]
        lines.append(f"| {s['season']} | {sh['count_90_plus']} | {sh['avg_ovr']} | {sh['avg_age']} | {s['exits']} | "
                     f"{s['league_term']:+g} | {s['star_term']:+g} |")
    c = result["careers"]
    lines += ["", f"Career targets from `{c['meta']['source']}` ({c['meta']['careers']} careers through "
              f"{c['meta']['last_season']}, {c['meta']['active']} active then): years-pro shares {c['years_share']}, "
              f"games in year k {c['games']}. Year-to-year retention from the same file ({c['retention_biased']}) is "
              "survivor-biased and not scored.", ""]
    return "\n".join(lines)


def write_fit(result, out=DEFAULT_OUT, snapshot_path=None):
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    payload = {"meta": {"built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                        "snapshot": snapshot_path, "applied": False,
                        "note": "recommendation only; engines/progression.py is not changed"},
               **{k: v for k, v in result.items() if k != "replay_after"}, "replay_after": result["replay_after"]}
    with open(out, "w") as f:
        json.dump(payload, f, indent=1, default=str)
    md = os.path.splitext(out)[0] + ".md"
    with open(md, "w") as f:
        f.write(format_report(result, snapshot_path))
    return out, md


def main(argv=None):
    ap = argparse.ArgumentParser(description="Fit engines/progression.py constants offline against a snapshot league "
                                             "(recommends; never edits the engine).")
    ap.add_argument("snapshot", help="pc_save_reader.mjs output JSON")
    ap.add_argument("--seasons", type=int, default=5)
    ap.add_argument("--budget", type=int, default=200, help="replays the search may run")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default=DEFAULT_OUT, help="fit JSON (the markdown report goes next to it)")
    ap.add_argument("--careers", default=CAREERS_CSV, help="career-totals CSV")
    ap.add_argument("--no-hold-shape", action="store_true", help="replay without the engine's league-shape term")
    args = ap.parse_args(argv)
    with open(args.snapshot) as f:
        snapshot = json.load(f)
    result = fit(snapshot, budget=args.budget, seed=args.seed, seasons=args.seasons,
                 careers=career_targets(args.careers), hold_shape=not args.no_hold_shape,
                 log=lambda m: print(m, file=sys.stderr, flush=True))
    out, md = write_fit(result, args.out, os.path.abspath(args.snapshot))
    print(f"score {result['score_before']:.4f} -> {result['score_after']:.4f} after {result['evaluations']} replays; "
          f"{len(result['recommended'])} constants recommended -> {out}, {md}")
    for r in result["recommended"][:5]:
        print(f"  {r['param']}: {r['current']} -> {r['recommended']} (gain {r['gain']:.4f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
