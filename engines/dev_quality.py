"""
Front-office player development score (PROJECT_SPEC §2.18, §3.3f). How much a team's players improved, beyond what
their age, rating and dev trait predicted, while they were on its roster. Narrative only (writers, hot seat later):
fo_quality, the draft score, is what drives scouting noise.

Window: one roster card per player per season (the Season scan's preseason roster). A window is a player's card in
season S-1 and his card in S: delta = OVR_S - OVR_(S-1), credited to the team on the S-1 card (he played S-1 for it
and the offseason progression after it shows up on the S card). A player who left still counts for that team, so a
front office can't hide the ones who got worse. Rookies need no special case: their first window starts at their
first roster card (years_of_experience 0), not the draft board, since the board-to-card change happens before they
play a snap. The 2035 card was scanned at the end of 2035, so the 2035 -> 2036 window is one offseason only and is
left out (FIRST_START_SEASON) unless include_partial.

Player score: r = (delta - E[delta]) / sd_band + 0.5 * (dev steps gained, both ends revealed).
  E[delta] is a ridge least-squares fit over every window in the league that season (age buckets at the start card,
  start-OVR bands <70 / 70-79 / 80-84 / 85-89 / 90-94 / 95+ so a 92 is compared with other 90-94s, start dev trait
  with Hidden/NULL as its own 'unknown' level, rookie flag). sd_band = the residual sd within the player's start-OVR
  band (the league's when the band has under 20 players), floor 0.75: a surprise is judged against how much players
  at that level normally move. Cap rule: a player who starts at 99 and stays at 99 can't score below 0 (he can't go
  up; ON TRACK at worst). Labels: DEVELOPED r >= 1 with a gain, REGRESSED r <= -2 with a drop, STALLED r <= -1 with no gain,
  else ON TRACK (a label never contradicts the ratings).
Team: weight 1.0 for the 22 best start-card OVRs on the team, 0.5 for the rest; windows ending S, S-1, S-2 weigh
1.0 / 0.6 / 0.35. D = sum W w r / sum W w, n = sum W w, D* = D n / (n + 20). Scores follow the GM: only windows
starting at or after the current GM's start_season count, so a new GM starts at D* 0. All 32 teams are scored and
ranked, JR's included. Stored on gm_team_seasons(S).

CLI: python3 -m engines.dev_quality --season 2037 [--include-partial] [--dry-run]
"""
import argparse
import json
import math
import sys

WINDOW_WEIGHTS = (1.0, 0.6, 0.35)       # windows ending S, S-1, S-2
FIRST_START_SEASON = 2036               # first card from a preseason scan (2035's was end of season)
STARTERS = 22
DEPTH_WEIGHT = 0.5
SHRINK_K = 20
SD_FLOOR = 0.75
MIN_BAND_N = 20
OVR_CAP = 99
RIDGE = 1.0
DEV_STEP_BONUS = 0.5
DEVELOPED, STALLED, REGRESSED = 1.0, -1.0, -2.0
AGE_BUCKETS = (22, 23, 24, 25, 26, 28, 29, 30, 31, 32, 33)   # <= 22 ... 33+; 27 is the base (no column)
DEV_RANK = {"NORMAL": 0, "STAR": 1, "SUPERSTAR": 2, "X-FACTOR": 3, "SUPERSTAR X-FACTOR": 3}
OVR_BANDS = (70, 80, 85, 90, 95)      # lower edges: <70, 70-79 (base, no column), 80-84, 85-89, 90-94, 95+


# ------------------------------------------------------------------ pure ----
def dev_rank(dev):
    """0-3 for a revealed dev, None for 'Hidden' (under 500 snaps) or NULL (never scanned)."""
    d = (dev or "").strip().upper()
    if not d or d == "HIDDEN":
        return None
    for k in sorted(DEV_RANK, key=len, reverse=True):
        if d == k or d.startswith(k):
            return DEV_RANK[k]
    return 0


def label(r, delta):
    """A label never contradicts the ratings: DEVELOPED needs a real gain, REGRESSED a real drop, STALLED no
    gain (a 31-year-old who held at 84 beat his expected decline, but he held steady: ON TRACK)."""
    if r >= DEVELOPED and delta > 0:
        return "DEVELOPED"
    if r <= REGRESSED and delta < 0:
        return "REGRESSED"
    if r <= STALLED and delta <= 0:
        return "STALLED"
    return "ON TRACK"


def age_bucket(age):
    if age is None:
        return 27
    a = min(max(int(age), 22), 33)
    return 27 if a == 27 else a


def ovr_band(ovr):
    """0 (<70), 1 (70-79), 2 (80-84), 3 (85-89), 4 (90-94), 5 (95+)."""
    return sum(1 for edge in OVR_BANDS if ovr >= edge)


def features(w):
    """Design row for one window: intercept, age buckets, start-OVR bands (70-79 the base), start dev
    (Star/Superstar/X-Factor/unknown), rookie."""
    b = age_bucket(w["age"])
    rank = dev_rank(w["dev_start"])
    return ([1.0] + [1.0 if b == a else 0.0 for a in AGE_BUCKETS] + [1.0 if ovr_band(w["ovr_start"]) == k else 0.0 for k in (0, 2, 3, 4, 5)]
            + [1.0 if rank == k else 0.0 for k in (1, 2, 3)] + [1.0 if rank is None else 0.0]
            + [1.0 if w["rookie"] else 0.0])


def fit_expected(windows):
    """Ridge least squares of delta on features -> (coefficients, {band: residual sd, None: league sd}). The intercept
    is not penalized; a band with under MIN_BAND_N players uses the league sd."""
    import numpy as np
    X = np.array([features(w) for w in windows], dtype=float)
    y = np.array([w["ovr_end"] - w["ovr_start"] for w in windows], dtype=float)
    pen = RIDGE * np.eye(X.shape[1])
    pen[0, 0] = 0.0
    beta = np.linalg.solve(X.T @ X + pen, X.T @ y)
    res = [float(v) for v in y - X @ beta]

    def sd(rs):
        return max(math.sqrt(sum(v * v for v in rs) / max(len(rs) - 1, 1)), SD_FLOOR) if rs else SD_FLOOR
    league = sd(res)
    by = {}
    for w, v in zip(windows, res):
        by.setdefault(ovr_band(w["ovr_start"]), []).append(v)
    sds = {b: sd(rs) if len(rs) >= MIN_BAND_N else league for b, rs in by.items()}
    sds[None] = league
    return beta, sds


def expected_delta(beta, w):
    return float(sum(b * x for b, x in zip(beta, features(w))))


def player_score(w, beta, sds):
    """-> (r, expected delta, dev steps). sds from fit_expected."""
    delta = w["ovr_end"] - w["ovr_start"]
    exp = expected_delta(beta, w)
    sd = sds.get(ovr_band(w["ovr_start"]), sds[None])
    a, b = dev_rank(w["dev_start"]), dev_rank(w["dev_end"])
    steps = b - a if a is not None and b is not None else 0
    r = (delta - exp) / sd + DEV_STEP_BONUS * steps
    if w["ovr_start"] >= OVR_CAP and w["ovr_end"] >= OVR_CAP:
        r = max(r, 0.0)                     # held at the cap: never a penalty
    return round(r, 3), round(exp, 2), steps


def starter_flags(windows):
    """True for each team's 22 best start-card OVRs in a window (ties by player_id)."""
    by = {}
    for w in windows:
        by.setdefault(w["team_id"], []).append(w)
    out = {}
    for ws in by.values():
        for i, w in enumerate(sorted(ws, key=lambda w: (-w["ovr_start"], w["player_id"]))):
            out[w["player_id"]] = i < STARTERS
    return out


def score_window(windows):
    """windows: every player with a rated card in both seasons of one window: {player_id, name, position, team_id,
    age, ovr_start, ovr_end, dev_start, dev_end, rookie}. -> scored rows (fit on all of them)."""
    if not windows:
        return []
    beta, sds = fit_expected(windows)
    starters = starter_flags(windows)
    out = []
    for w in windows:
        r, exp, steps = player_score(w, beta, sds)
        out.append(dict(w, r=r, expected=exp, delta=w["ovr_end"] - w["ovr_start"], dev_steps=steps,
                        starter=starters[w["player_id"]], label=label(r, w["ovr_end"] - w["ovr_start"])))
    return out


def team_score(scored_by_window):
    """scored_by_window: {window_age (0 = ends S): [scored rows for one team]} -> {D, D_star, n, players}."""
    num = den = 0.0
    for age, rows in scored_by_window.items():
        if not 0 <= age < len(WINDOW_WEIGHTS):
            continue
        for p in rows:
            wt = WINDOW_WEIGHTS[age] * (1.0 if p["starter"] else DEPTH_WEIGHT)
            num += wt * p["r"]
            den += wt
    if not den:
        return {"D": None, "D_star": None, "n": 0.0}
    D = num / den
    return {"D": round(D, 3), "D_star": round(D * den / (den + SHRINK_K), 3), "n": round(den, 2)}


def tier(rank, teams):
    """Writer wording from the league rank only."""
    if rank is None:
        return None
    if rank <= max(teams // 4, 1):
        return "among the league's best at developing players"
    if rank > teams - max(teams // 4, 1):
        return "among the league's worst at developing players"
    return "around the middle of the league at developing players"


# -------------------------------------------------------------------- db ----
def fetch_window(cur, end_season):
    """Players with a rated card in end_season-1 (on a team) and in end_season."""
    cur.execute("""SELECT a.player_id, COALESCE(p.full_name, p.player_name), a.position, a.team_id, a.age,
                          a.overall_rating, b.overall_rating, a.dev_trait, b.dev_trait,
                          COALESCE(a.years_of_experience, 1) = 0
                   FROM player_seasons a
                   JOIN player_seasons b ON b.player_id = a.player_id AND b.season = a.season + 1
                   JOIN players p ON p.id = a.player_id
                   WHERE a.season = %s AND a.team_id IS NOT NULL
                     AND a.overall_rating IS NOT NULL AND b.overall_rating IS NOT NULL""", (end_season - 1,))
    cols = ("player_id", "name", "position", "team_id", "age", "ovr_start", "ovr_end", "dev_start", "dev_end",
            "rookie")
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def windows_for(season, include_partial=False):
    """End seasons of the windows that count for `season`, newest first, as (window_age, end_season)."""
    return [(i, season - i) for i in range(len(WINDOW_WEIGHTS))
            if include_partial or season - i - 1 >= FIRST_START_SEASON]


def in_tenure(windows_by_age, season, start_season):
    """Only the windows the current GM ran: a window ending E starts at the E-1 card, so it counts when
    E - 1 >= start_season. A new GM starts at the baseline (D* 0)."""
    return {age: rows for age, rows in windows_by_age.items()
            if start_season is None or season - age - 1 >= start_season}


def compute(cur, season, include_partial=False, log=print):
    """dev_quality for every team with an open GM, from the current GMs' tenures, ranked over all 32 (JR's teams
    included, JR 2026-10-21); writes nothing (the hot seat calls it before the season's gm_team_seasons rows
    exist). {} when no window counts yet."""
    from engines.fo_quality import current_gms
    wins = windows_for(season, include_partial)
    if not wins:
        log(f"dev_quality {season}: no full window yet (the first starts at the {FIRST_START_SEASON} card)")
        return {}
    by_team = {}
    used = []
    for age, end in wins:
        scored = score_window(fetch_window(cur, end))
        if scored:
            used.append(end)
            log(f"  window {end - 1}->{end}: {len(scored)} players scored")
        for p in scored:
            by_team.setdefault(p["team_id"], {}).setdefault(age, []).append(p)
    out = {}
    for tid, g in current_gms(cur).items():
        mine = in_tenure(by_team.get(tid, {}), season, g["start_season"])
        res = team_score(mine)
        res.update(abbr=g["abbr"], held=g["held"], gm_id=g["gm_id"], start_season=g["start_season"],
                   windows=mine, used=used)
        out[tid] = res
    ranked = sorted((r for r in out.values() if r["D_star"] is not None), key=lambda r: -r["D_star"])
    for i, r in enumerate(ranked, 1):
        r["rank"] = i
        r["teams_ranked"] = len(ranked)
    return out


def recompute(cur, season, include_partial=False, dry_run=False, log=print):
    """dev_quality for every team (JR's included), written to gm_team_seasons(season) (dev_quality,
    dev_quality_inputs, dev_quality_at). Idempotent. Returns {team_id: result}; empty when no window counts yet."""
    cur.execute("SELECT team_id FROM gm_team_seasons WHERE season = %s", (season,))
    have = {r[0] for r in cur.fetchall()}
    if not have:
        raise SystemExit(f"no gm_team_seasons rows for {season}")
    out = {t: r for t, r in compute(cur, season, include_partial, log).items() if t in have}
    if not out:
        return {}
    for res in sorted(out.values(), key=lambda r: r.get("rank") or 99):
        newest = res["windows"].get(min(res["windows"]), []) if res["windows"] else []
        best = sorted(newest, key=lambda p: -p["r"])[:2]
        tag = f"#{res.get('rank', '-')} D* {res['D_star']}" + (" (JR)" if res["held"] else "")
        log(f"  {res['abbr']:<4} {tag:<28} n={res['n']}  " +
            ", ".join(f"{p['name']} {p['ovr_start']}->{p['ovr_end']} {p['label']}" for p in best))
    used = next(iter(out.values()))["used"]
    log(f"dev_quality {season}: {sum(1 for r in out.values() if r.get('rank'))} teams scored over "
        f"{len(used)} window(s)")
    if dry_run:
        log("  dry run: nothing written")
        return out
    for tid, res in out.items():
        players = [{k: p[k] for k in ("player_id", "name", "position", "age", "ovr_start", "ovr_end", "delta",
                                      "expected", "dev_start", "dev_end", "dev_steps", "rookie", "starter", "r",
                                      "label")} | {"window": f"{season - age - 1}-{season - age}"}
                   for age, rows in sorted(res["windows"].items()) for p in rows]
        inputs = {"season": season, "gm_id": res["gm_id"], "gm_start_season": res["start_season"], "D": res["D"],
                  "D_star": res["D_star"], "n": res["n"], "rank": res.get("rank"),
                  "teams_ranked": res.get("teams_ranked"),
                  "windows": [f"{e - 1}-{e}" for e in used if season - e in res["windows"]],
                  "weights": WINDOW_WEIGHTS, "players": players}
        cur.execute("""UPDATE gm_team_seasons SET dev_quality = %s, dev_quality_inputs = %s, dev_quality_at = now()
                       WHERE team_id = %s AND season = %s""",
                    (res["D_star"], json.dumps(inputs), tid, season))
    return out


def team_development_record(cur, team_id, season=None):
    """Writer facts: the team's league tier and its biggest risers and fallers from the newest window, from the
    latest stored inputs. Labels, ratings and tier only: never D, D* or r."""
    cur.execute("""SELECT season, dev_quality_inputs FROM gm_team_seasons
                   WHERE team_id = %s AND dev_quality_inputs IS NOT NULL AND (%s::int IS NULL OR season <= %s)
                   ORDER BY season DESC LIMIT 1""", (team_id, season, season))
    r = cur.fetchone()
    if not r:
        return None
    inputs = r[1] if isinstance(r[1], dict) else json.loads(r[1])
    newest = (inputs.get("windows") or [None])[0]
    rows = [p for p in inputs.get("players", []) if p.get("window") == newest]
    keep = ("name", "position", "age", "ovr_start", "ovr_end", "dev_start", "dev_end", "rookie", "label")
    risers = [{k: p[k] for k in keep} for p in sorted(rows, key=lambda p: -p["r"]) if p["label"] == "DEVELOPED"][:3]
    fallers = [{k: p[k] for k in keep} for p in sorted(rows, key=lambda p: p["r"])
               if p["label"] in ("STALLED", "REGRESSED")][:3]
    return {"season": r[0], "window": newest, "tier": tier(inputs.get("rank"), inputs.get("teams_ranked") or 0),
            "risers": risers, "fallers": fallers}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Front-office player development score (dev_quality)")
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--include-partial", action="store_true",
                    help=f"also score windows starting before {FIRST_START_SEASON} (end-of-season cards)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    import psycopg
    from pipeline.load_capture import DSN
    with psycopg.connect(DSN) as conn:
        recompute(conn.cursor(), a.season, a.include_partial, a.dry_run)
        conn.rollback() if a.dry_run else conn.commit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
