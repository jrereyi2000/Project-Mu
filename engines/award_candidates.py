"""Award candidates (docs/in_progress/OWNERS_SCOUTS_VOTERS_PLAN.md §2.5): stat-based candidate scores for the six
major awards and for every All-Pro slot, which the media panel (newsroom/award_ballots.py) votes on.

Each candidate is {id, name, team_id, position, games, metrics: {...}, base_score}. Metrics are z-scores within
the comparison pool, so the voters' leans (weights on the metric names) are on one scale:

  production   position-adjusted volume (offense: within the player's position group; defense: a weighted line)
  efficiency   passer rating (QB), yards a carry (RB), yards a catch (WR/TE), FG% (K), net average (P),
               return average (KR/PR)
  wins         his team's regular-season wins, z across teams
  record_pace  max(0, production - 2): an outlier season
  rating       his roster overall (player_seasons.overall_rating): the reputation term, used mostly for linemen,
               long snappers and special teamers, whose stats say little

MVP/OPOY: offensive players; DPOY: defenders; OROY/DROY: the same, rookies only (years_of_experience 0); COTY:
coty_scores (engines/coty_engine.py), base = (coty_score - 50) / 10. Minimum games rule: a player needs MIN_GAMES
games played (season_stat_general); a player with no games row counts as eligible (the capture may lack it).

  fetch_lines(cur, season) -> [line]          one dict per player-season with every stat the metrics read
  fetch_team_wins(cur, season) -> {team_id: wins}
  candidates_from_lines(lines, award, team_wins) -> [candidate], best base first   (pure)
  all_pro_candidates_from_lines(lines, team_wins) -> {slot: [candidate]}           (pure)
  candidates(cur, season, award) / all_pro_candidates(cur, season)

All weights are review-only estimates (R3: tuned from synthetic seasons in tests/test_award_ballots.py).
"""
from __future__ import annotations

import statistics

from newsroom.all_pro import ALL_SLOT_QUOTAS, SLOT_COMPATIBLE_POSITIONS

AWARDS = ("MVP", "OPOY", "DPOY", "OROY", "DROY", "COTY")
PLAYER_AWARDS = AWARDS[:5]
MIN_GAMES = 10                 # of 17
POOL = 15                      # candidates per award the panel sees
SLOT_POOL = 12                 # candidates per All-Pro slot
METRICS = ("production", "efficiency", "wins", "record_pace", "rating")

QB_POS = {"QB"}
RB_POS = {"HB", "RB", "FB"}
WR_POS = {"WR"}
TE_POS = {"TE"}
OFFENSE_POS = QB_POS | RB_POS | WR_POS | TE_POS
DEFENSE_POS = {"LEDG", "REDG", "LE", "RE", "DE", "EDGE", "DT", "NT", "DL", "LOLB", "ROLB", "MLB", "MIKE", "WILL",
               "SAM", "LB", "OLB", "ILB", "CB", "DB", "FS", "SS", "S"}

# base score weights per award (the panel's neutral blend; voters lean them)
AWARD_WEIGHTS = {
    "MVP": {"production": 0.50, "wins": 0.30, "efficiency": 0.20},
    "OPOY": {"production": 0.65, "wins": 0.10, "efficiency": 0.25},
    "DPOY": {"production": 0.85, "wins": 0.15},
    "OROY": {"production": 0.65, "wins": 0.10, "efficiency": 0.25},
    "DROY": {"production": 0.85, "wins": 0.15},
    "COTY": {"production": 1.0},
}
MVP_POSITION = {"QB": 0.6, "TE": -0.2}           # MVP voters favour quarterbacks

# defensive line: points per stat, and per All-Pro slot
DEFENSE_POINTS = {"sacks": 1.0, "ints": 1.5, "tfl": 0.5, "deflections": 0.4, "forced_fumbles": 1.0,
                  "tackles": 0.05}
SLOT_DEFENSE_POINTS = {
    "EDGE": {"sacks": 1.5, "tfl": 0.6, "forced_fumbles": 1.0, "tackles": 0.03},
    "DI": {"sacks": 1.3, "tfl": 0.8, "tackles": 0.05, "forced_fumbles": 0.8},
    "LB": {"tackles": 0.12, "tfl": 0.5, "sacks": 0.8, "ints": 1.5, "deflections": 0.4, "forced_fumbles": 0.8},
    "CB": {"ints": 2.0, "deflections": 0.8, "tackles": 0.03, "tds": 2.0},
    "SCB": {"ints": 1.5, "deflections": 0.7, "tackles": 0.06, "tfl": 0.4},
    "S": {"ints": 1.8, "deflections": 0.6, "tackles": 0.07, "forced_fumbles": 0.8},
}
SLOT_POSITIONS = {**{k: (set(v) if v is not None else None) for k, v in SLOT_COMPATIBLE_POSITIONS.items()}}
SLOT_RATING_WEIGHT = {"LT": 0.7, "LG": 0.7, "C": 0.7, "RG": 0.7, "RT": 0.7, "LS": 1.0, "ST": 0.8}
SLOT_DEFAULT_WEIGHTS = {"production": 0.6, "efficiency": 0.2, "wins": 0.1, "rating": 0.1}


def _num(v):
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def zscores(values: dict) -> dict:
    """{key: z}; 0 for everyone when fewer than two values or no spread."""
    if len(values) < 2:
        return {k: 0.0 for k in values}
    mean, sd = statistics.fmean(values.values()), statistics.pstdev(values.values())
    if sd == 0:
        return {k: 0.0 for k in values}
    return {k: (v - mean) / sd for k, v in values.items()}


def eligible(line) -> bool:
    g = line.get("games")
    return g is None or g >= MIN_GAMES


def position_group(pos):
    pos = (pos or "").upper()
    for name, group in (("QB", QB_POS), ("RB", RB_POS), ("WR", WR_POS), ("TE", TE_POS)):
        if pos in group:
            return name
    return "DEF" if pos in DEFENSE_POS else None


def offense_production(line) -> float:
    g = _num
    group = position_group(line.get("position"))
    if group == "QB":
        return (0.04 * g(line.get("pass_yards")) + 4 * g(line.get("pass_tds")) - 2 * g(line.get("interceptions"))
                + 0.1 * g(line.get("rush_yards")) + 6 * g(line.get("rush_tds")))
    return (0.1 * (g(line.get("rush_yards")) + g(line.get("rec_yards"))) + 6 * (g(line.get("rush_tds"))
            + g(line.get("rec_tds"))) + 0.5 * g(line.get("receptions")) - 2 * g(line.get("fumb")))


def offense_efficiency(line) -> float:
    group = position_group(line.get("position"))
    if group == "QB":
        return _num(line.get("pass_rating"))
    if group == "RB":
        att = _num(line.get("rush_attempts"))
        return _num(line.get("rush_yards")) / att if att else 0.0
    rec = _num(line.get("receptions"))
    return _num(line.get("rec_yards")) / rec if rec else 0.0


def defense_production(line, points=DEFENSE_POINTS) -> float:
    return sum(w * _num(line.get(k)) for k, w in points.items())


def _candidate(line, metrics, base):
    return {"id": line["player_id"], "name": line.get("name") or "", "team_id": line.get("team_id"),
            "position": line.get("position"), "games": line.get("games"),
            "metrics": {k: round(v, 4) for k, v in metrics.items()}, "base_score": round(base, 4)}


def _blend(metrics, weights):
    total = sum(weights.values())
    return sum(w * metrics.get(k, 0.0) for k, w in weights.items()) / total if total else 0.0


def _wins_z(team_wins):
    return zscores({t: float(w) for t, w in (team_wins or {}).items()})


def candidates_from_lines(lines, award, team_wins) -> list:
    """Pure. The award's eligible candidates, best base score first (all of them; callers keep POOL)."""
    if award not in PLAYER_AWARDS:
        raise ValueError(f"unknown player award {award!r}")
    rookies = award in ("OROY", "DROY")
    defense = award in ("DPOY", "DROY")
    pool = [ln for ln in lines if eligible(ln) and (not rookies or ln.get("rookie"))
            and (position_group(ln.get("position")) == "DEF") == defense
            and position_group(ln.get("position")) is not None]
    wz = _wins_z(team_wins)
    prod, eff = {}, {}
    if defense:
        prod = zscores({ln["player_id"]: defense_production(ln) for ln in pool})
    else:
        for group in ("QB", "RB", "WR", "TE"):
            members = [ln for ln in pool if position_group(ln.get("position")) == group]
            prod.update(zscores({ln["player_id"]: offense_production(ln) for ln in members}))
            eff.update(zscores({ln["player_id"]: offense_efficiency(ln) for ln in members}))
    out = []
    for ln in pool:
        pid = ln["player_id"]
        m = {"production": prod.get(pid, 0.0), "efficiency": eff.get(pid, 0.0),
             "wins": wz.get(ln.get("team_id"), 0.0)}
        m["record_pace"] = max(0.0, m["production"] - 2.0)
        base = _blend(m, AWARD_WEIGHTS[award])
        if award == "MVP":
            base += MVP_POSITION.get(position_group(ln.get("position")), 0.0)
        out.append(_candidate(ln, m, base))
    out.sort(key=lambda c: (-c["base_score"], c["id"]))
    return out


def coty_candidates_from_rows(rows) -> list:
    """rows: coty_scores rows {coach_id, coach_name, team_id, coty_score, win_pct, wins_above_expected}."""
    out = []
    for r in rows:
        prod = (_num(r.get("coty_score")) - 50.0) / 10.0
        out.append({"id": r["coach_id"], "name": r.get("coach_name") or "", "team_id": r.get("team_id"),
                    "position": "HC", "games": None,
                    "metrics": {"production": round(prod, 4), "efficiency": 0.0,
                                "wins": round(_num(r.get("win_pct")) * 4 - 2, 4), "record_pace": 0.0},
                    "base_score": round(prod, 4)})
    out.sort(key=lambda c: (-c["base_score"], c["id"]))
    return out


# ------------------------------------------------------------------------------------------------ All-Pro ---

def _slot_line_ok(slot, line):
    allowed = SLOT_POSITIONS.get(slot)
    return allowed is None or (line.get("position") or "").upper() in allowed


def _slot_raw(slot, line):
    """(production, efficiency) raw values for one slot."""
    g = _num
    if slot in ("QB", "RB", "FB", "WR", "TE"):
        return offense_production(line), offense_efficiency(line)
    if slot == "AP":
        ret = g(line.get("kr_yards")) + g(line.get("pr_yards"))
        return offense_production(line) + 0.05 * ret, 0.0
    if slot in SLOT_DEFENSE_POINTS:
        return defense_production(line, SLOT_DEFENSE_POINTS[slot]), 0.0
    if slot in ("LT", "LG", "C", "RG", "RT"):
        downs = g(line.get("downs_played"))
        return -g(line.get("sacks_allowed")), (downs / 100.0)
    if slot == "K":
        return g(line.get("fgm")) * 3 + g(line.get("xpm")), g(line.get("fg_pct"))
    if slot == "P":
        return g(line.get("punts_in20")) + 0.01 * g(line.get("punt_yards")), g(line.get("punt_net_avg"))
    if slot in ("KR", "PR"):
        p = slot.lower()
        return g(line.get(f"{p}_yards")) * 0.1 + 6 * g(line.get(f"{p}_tds")), g(line.get(f"{p}_avg"))
    if slot == "ST":
        return g(line.get("st_tackles")), 0.0
    return 0.0, 0.0                                  # LS: reputation only


def _slot_has_stats(slot, line):
    g = _num
    if slot == "K":
        return g(line.get("fga")) > 0
    if slot == "P":
        return g(line.get("punts")) > 0
    if slot in ("KR", "PR"):
        return g(line.get(f"{slot.lower()}_returns")) > 0
    return True


def all_pro_candidates_from_lines(lines, team_wins) -> dict:
    """Pure. {slot: [candidate]} for every All-Pro slot, best base score first, at most SLOT_POOL each."""
    wz = _wins_z(team_wins)
    out = {}
    for slot in ALL_SLOT_QUOTAS:
        pool = [ln for ln in lines if eligible(ln) and _slot_line_ok(slot, ln) and _slot_has_stats(slot, ln)]
        raw = {ln["player_id"]: _slot_raw(slot, ln) for ln in pool}
        pz = zscores({k: v[0] for k, v in raw.items()})
        ez = zscores({k: v[1] for k, v in raw.items()})
        rz = zscores({ln["player_id"]: _num(ln.get("overall_rating")) for ln in pool})
        rating_w = SLOT_RATING_WEIGHT.get(slot, 0.1)
        weights = dict(SLOT_DEFAULT_WEIGHTS, rating=rating_w)
        if rating_w >= 0.7:
            weights.update(production=1.0 - rating_w, efficiency=0.0, wins=0.0)
        cands = []
        for ln in pool:
            pid = ln["player_id"]
            m = {"production": pz.get(pid, 0.0), "efficiency": ez.get(pid, 0.0),
                 "wins": wz.get(ln.get("team_id"), 0.0), "rating": rz.get(pid, 0.0)}
            m["record_pace"] = max(0.0, m["production"] - 2.0)
            c = _candidate(ln, m, _blend(m, weights))
            c["slot"] = slot
            cands.append(c)
        cands.sort(key=lambda c: (-c["base_score"], c["id"]))
        out[slot] = cands[:SLOT_POOL]
    return out


# ------------------------------------------------------------------------------------------------ fetches ---

def fetch_team_wins(cur, season) -> dict:
    cur.execute("""SELECT home_team_id, away_team_id, home_score, away_score FROM games
                   WHERE season = %s AND COALESCE(game_type, 'REGULAR_SEASON') = 'REGULAR_SEASON'
                     AND status = 'COMPLETED'""", (season,))
    wins = {}
    for h, a, hs, as_ in cur.fetchall():
        wins.setdefault(h, 0.0)
        wins.setdefault(a, 0.0)
        if hs > as_:
            wins[h] += 1
        elif as_ > hs:
            wins[a] += 1
        else:
            wins[h] += 0.5
            wins[a] += 0.5
    return wins


LINE_SQL = """
SELECT ps.player_id, COALESCE(p.full_name, p.player_name), ps.team_id, ps.position, ps.overall_rating,
       COALESCE(ps.years_of_experience, 99) = 0 AS rookie, g.games_played, g.downs_played,
       s.pass_yards, s.pass_tds, s.interceptions, s.pass_rating, s.rush_yards, s.rush_tds, s.rush_attempts,
       s.receptions, s.rec_yards, s.rec_tds, s.fumb,
       d.tackles, d.tfl, d.sacks, d.ints, d.deflections, d.forced_fumbles, d.tds,
       k.fgm, k.fga, k.fg_pct, k.xpm, pu.punts, pu.yards, pu.net_average, pu.inside_20,
       kr.returns, kr.yards, kr.average, kr.tds, pr.returns, pr.yards, pr.average, pr.tds, b.sacks_allowed
FROM player_seasons ps
JOIN players p ON p.id = ps.player_id
LEFT JOIN season_stat_general g ON g.season = ps.season AND g.player_id = ps.player_id
LEFT JOIN (SELECT player_id, sum(pass_yards) pass_yards, sum(pass_tds) pass_tds, sum(interceptions) interceptions,
                  max(pass_rating) pass_rating, sum(rush_yards) rush_yards, sum(rush_tds) rush_tds,
                  sum(rush_attempts) rush_attempts, sum(receptions) receptions, sum(rec_yards) rec_yards,
                  sum(rec_tds) rec_tds, sum(fumb) fumb
           FROM season_stats WHERE season = %(s)s AND player_id IS NOT NULL GROUP BY player_id) s
       ON s.player_id = ps.player_id
LEFT JOIN season_stat_defense d ON d.season = ps.season AND d.player_id = ps.player_id
LEFT JOIN season_stat_kicking k ON k.season = ps.season AND k.player_id = ps.player_id
LEFT JOIN season_stat_punting pu ON pu.season = ps.season AND pu.player_id = ps.player_id
LEFT JOIN season_stat_kickreturn kr ON kr.season = ps.season AND kr.player_id = ps.player_id
LEFT JOIN season_stat_puntreturn pr ON pr.season = ps.season AND pr.player_id = ps.player_id
LEFT JOIN season_stat_blocking b ON b.season = ps.season AND b.player_id = ps.player_id
WHERE ps.season = %(s)s AND ps.team_id IS NOT NULL
"""
LINE_FIELDS = ("player_id", "name", "team_id", "position", "overall_rating", "rookie", "games", "downs_played",
               "pass_yards", "pass_tds", "interceptions", "pass_rating", "rush_yards", "rush_tds", "rush_attempts",
               "receptions", "rec_yards", "rec_tds", "fumb", "tackles", "tfl", "sacks", "ints", "deflections",
               "forced_fumbles", "tds", "fgm", "fga", "fg_pct", "xpm", "punts", "punt_yards", "punt_net_avg",
               "punts_in20", "kr_returns", "kr_yards", "kr_avg", "kr_tds", "pr_returns", "pr_yards", "pr_avg",
               "pr_tds", "sacks_allowed")


def fetch_lines(cur, season) -> list:
    cur.execute(LINE_SQL, {"s": season})
    return [dict(zip(LINE_FIELDS, row)) for row in cur.fetchall()]


def fetch_coty_rows(cur, season) -> list:
    cur.execute("""SELECT cs.coach_id, c.coach_name, cs.team_id, cs.coty_score, cs.win_pct, cs.wins_above_expected
                   FROM coty_scores cs JOIN coaches c ON c.id = cs.coach_id WHERE cs.season = %s""", (season,))
    return [dict(zip(("coach_id", "coach_name", "team_id", "coty_score", "win_pct", "wins_above_expected"), r))
            for r in cur.fetchall()]


def candidates(cur, season, award) -> list:
    """The award's top POOL candidates from the database."""
    if award == "COTY":
        return coty_candidates_from_rows(fetch_coty_rows(cur, season))[:POOL]
    return candidates_from_lines(fetch_lines(cur, season), award, fetch_team_wins(cur, season))[:POOL]


def all_pro_candidates(cur, season) -> dict:
    return all_pro_candidates_from_lines(fetch_lines(cur, season), fetch_team_wins(cur, season))
