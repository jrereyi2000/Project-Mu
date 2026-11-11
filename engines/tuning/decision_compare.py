"""
Decision comparison report for a simulated season (docs/CLOSE_OUT_CHECKLIST.md P3-07).

  python3 -m engines.tuning.decision_compare --dsn DSN --season 2026 [--targets PATH] [--out-dir DIR]

engines/tuning/compare.py scores a season's *stat* output against the real-NFL stat bands. This module scores
the *decisions* behind it against data/tuning/decision_targets.json, using the gates in
docs/reference/TUNING_ACCEPTANCE_THRESHOLDS.md:
  §1  how a gate is scored (band / median ± tolerance / rate ± relative tolerance; no silent pass)
  §2  A1-A7, the decided gates (career length, retirement age, head-coach changes, trades, team movement,
      draft by round, rookie usage)
  §4  the decisions with no dataset yet — listed as missing, never faked
  §5  the quantities rejected as gates — reported, never scored

Only the gates §2 marks as decided get a PASS/FAIL. A quantity the database cannot measure is NOT MEASURED with
a reason; that is a failure of the run's coverage, not a silent pass (the overall verdict is INCOMPLETE).

Mu-side sources, per gate:
  A1 career length         player_retirements + player_seasons (years_of_experience, else distinct seasons);
                           the drafted cohort from players.draft_round 1-7 (Madden's PLYR_DRAFTROUND,
                           TUNING_ACCEPTANCE_THRESHOLDS.md §7 decision 10), not draft_class/draft_picks
  A2 retirement age        player_retirements + the age on that season's player_seasons row
  A3 head-coach changes    coach_tenures (head coaches only; coordinator_tenures is a different role)
  A4 trades                gm_trade_proposals APPROVED/EXECUTED; the in-season gate also needs the weekly
                           league office to have run (league_office_runs REGULAR_WEEK), else the engine never
                           saw weeks 2-18 and the count is not a season's worth of decisions
  A5 team movement         player_season_transitions MOVED, joined to games played both seasons (>= 8 each);
                           when the season ledger has not run, the team change is read from player_seasons
  A6 draft by round        draft_prospects actual picks linked to players; "started season" = a week at
                           depth 1 in depth_charts
  A7 rookie usage          first-season games of drafted skill players, excluding linemen (no stat line)

Output: decisions_<season>.md + .json beside the stat report (data/tuning/seasons/<run>/ for a run database).
"""
import argparse
import datetime as dt
import json
import os
import sys
from dataclasses import dataclass, field

from engines.tuning import decision_targets as D

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
TARGETS_DEFAULT = os.path.join(ROOT, "data", "tuning", "decision_targets.json")
SEASONS_DIR = os.path.join(ROOT, "data", "tuning", "seasons")

MIN_COHORT = D.MIN_COHORT                 # a group below this reports nothing rather than a misleading gate
TRADE_STATUSES = ("APPROVED", "EXECUTED")  # a proposal that became a deal
REGULAR_WEEKS = 18                        # the in-season window is Week 1 - the last game of the season
MEDIAN_TOL = 1.0                          # §7 decision 3: median +/-1 season
MEDIAN_TOL_LONG = 1.5                     # ... +/-1.5 for QB/K/P
AGE_TOL = 1.5                             # A2: median +/-1.5 years
SHARE_TOL = 0.10                          # shares +/-10 points
RATE_TOL = 0.20                           # rates +/-20% relative
ROOKIE_MEDIAN_TOL = 2.0                   # A7: median games +/-2
LONG_MEDIAN_GROUPS = ("QB", "K", "P")

# Madden 27 positions -> decision_targets' position groups (D.group_of handles the nflverse spellings)
MU_GROUPS = {
    "QB": {"QB"}, "RB": {"HB", "FB", "RB"}, "WR": {"WR"}, "TE": {"TE"},
    "OL": {"LT", "LG", "C", "RG", "RT", "OT", "OG", "T", "G", "OL"},
    "DL": {"LE", "RE", "DT", "NT", "LEDG", "REDG", "DE", "DL"},
    "LB": {"LOLB", "MLB", "ROLB", "MIKE", "SAM", "WILL", "OLB", "ILB", "LB"},
    "DB": {"CB", "FS", "SS", "DB", "S"},
    "K": {"K"}, "P": {"P"},
}
# the real rookie-usage cohort excludes linemen (they have no stat line)

PASS, FAIL = "PASS", "FAIL"
NOT_MEASURED, INCOMPLETE, REPORT_ONLY = "NOT MEASURED", "INCOMPLETE", "REPORT ONLY"

# TUNING_ACCEPTANCE_THRESHOLDS.md §4: decisions with no dataset yet. Listed, never computed.
NOT_YET_COMPUTABLE = (
    ("P3-06a Mu's own inflow", "sets the attainable career length (roster slots / entrants a season)",
     "count drafted + generated UDFA players entering per simulated season"),
    ("GM changes per season", "owner_hiring / GM hot seat", "no open dataset; curate, or use HC changes as proxy"),
    ("Coordinator changes per season", "owner_hiring, coaching_tree_hiring",
     "no open dataset; same route"),
    ("Blockbuster trade rate", "MAX_BLOCKBUSTERS", "join trades.csv pfr_id to draft_picks.w_av or Pro Bowl honours"),
    ("Holdouts / public trade requests", "trade_requests, player leaks", "reported events only; a curated table"),
    ("Contract extensions per season", "contracts_engine", "nflverse contract data, not in the current cache"),
)
# §5: quantities that look like targets but are not gated, with what covers them instead.
REPORTED_NOT_GATED = (
    ("Win-loss records, playoff seeds, award winners",
     "outputs of every engine at once, and noisy; the league-shape and per-stat gates cover them"),
    ("Free-agent dollars per season",
     "derived from cap growth, market curves and structure; the team-movement rate covers the market"),
    ("Retirement announcement timing",
     "Mu decides retirements at one offseason stage by design; retirement age and career length cover it"),
)


def mu_group_of(position):
    """Mu's position group for a Madden 27 position (e.g. LEDG -> DL, MIKE -> LB), or None."""
    if not position:
        return None
    p = str(position).upper().strip()
    for grp, members in MU_GROUPS.items():
        if p in members:
            return grp
    return None


# ------------------------------------------------------------------ pure extractors

def coach_changes(tenures, season):
    """Head-coach changes around `season` from (team_id, coach_id, start_season, end_season|None) rows.

    The first coach of a season is the earliest-starting tenure covering it, the last the latest (the same
    rule the real metric gets from the first and last game's coach). Returns
      between_seasons  teams whose last coach of `season` is not the first coach of `season + 1` — the hires
                       the season's Black Monday and hiring cycle made
      midseason        teams with two or more distinct head coaches inside `season`
      teams            teams with a head coach in `season`
    """
    def covering(s):
        by_team = {}
        for team, coach, start, end in tenures:
            if start <= s and (end is None or end >= s):
                by_team.setdefault(team, []).append((start, coach))
        return {t: (min(v)[1], max(v)[1]) for t, v in by_team.items() if v}

    cur, nxt = covering(season), covering(season + 1)
    between = sum(1 for t, (_first, last) in cur.items() if t in nxt and last != nxt[t][0])
    mid = sum(1 for first, last in cur.values() if first != last)
    return {"between_seasons": between, "midseason": mid, "teams": len(cur)}


def trade_counts(rows, season, last_week=23):
    """Approved trades in `season` from (season, week, status, trade_window) gm_trade_proposals rows.

    in_season is Weeks 1 - the season's last game (the §2 A4 gate, cutdown week excluded); offseason is
    every other approved row (OFFSEASON, DRAFT_DAY). `proposed` counts every row for the season, whatever
    its status, so a season where the engine barely ran is visible.
    """
    season_rows = [r for r in rows if r[0] == season]
    approved = [r for r in season_rows if r[2] in TRADE_STATUSES]
    in_season = [r for r in approved
                 if r[3] == "IN_SEASON" and 1 <= (r[1] or 0) <= last_week]
    offseason = [r for r in approved if r not in in_season]
    by_status = {}
    for r in season_rows:
        by_status[r[2]] = by_status.get(r[2], 0) + 1
    return {"in_season": len(in_season), "offseason": len(offseason), "all": len(approved),
            "proposed": len(season_rows), "by_status": by_status}


def _group_bands(pool):
    """{group: decision_targets.band} for every group with a cohort (small ones still report n)."""
    out = {}
    for grp in D.GROUP_ORDER:
        vals = pool.get(grp) or []
        if vals:
            out[grp] = D.band(vals)
    return out


def career_lengths(rows, drafted_ids):
    """Career lengths of retired players from (player_id, retired_season, row_season, position, yoe) rows.

    Length is seasons since debut: max(years_of_experience) + 1 when the save carries it (a veteran loaded
    at the 2026 start keeps his real length), else the number of distinct seasons the tuning database has.
    Returns {"drafted"|"all_players": {group: band}, "careers": n}.
    """
    by_player = {}
    for pid, retired, row_season, position, yoe in rows:
        e = by_player.setdefault(pid, {"retired": retired, "seasons": set(), "pos": None,
                                       "pos_season": None, "yoe": []})
        e["seasons"].add(row_season)
        if position and (e["pos_season"] is None or row_season >= e["pos_season"]):
            e["pos"], e["pos_season"] = position, row_season
        if yoe is not None:
            e["yoe"].append(yoe)
    pools = {"drafted": {}, "all_players": {}}
    for pid, e in by_player.items():
        length = (max(e["yoe"]) + 1) if e["yoe"] else len(e["seasons"])
        grp = mu_group_of(e["pos"])
        if not grp:
            continue
        pools["all_players"].setdefault(grp, []).append(length)
        if pid in drafted_ids:
            pools["drafted"].setdefault(grp, []).append(length)
    return {"drafted": _group_bands(pools["drafted"]),
            "all_players": _group_bands(pools["all_players"]),
            "careers": len(by_player)}


def retirement_ages(rows, drafted_ids):
    """Retirement ages from (player_id, retired_season, position, age|None) rows -> per-population bands."""
    pools = {"drafted": {}, "all_players": {}}
    n = 0
    for pid, _season, position, age in rows:
        grp = mu_group_of(position)
        if grp is None or age is None:
            continue
        n += 1
        pools["all_players"].setdefault(grp, []).append(float(age))
        if pid in drafted_ids:
            pools["drafted"].setdefault(grp, []).append(float(age))
    return {"drafted": _group_bands(pools["drafted"]),
            "all_players": _group_bands(pools["all_players"]),
            "n": n}


def movement_counts(rows):
    """Players changing teams between two consecutive seasons.

    rows: (player_id, season, team_id|None, games). Eligible = a team and >= 8 games in both seasons, the
    real metric's rule. Returns {"from_season", "to_season", "eligible", "moved", "rate"} or None when the
    rows don't hold exactly two seasons.
    """
    seasons = sorted({r[1] for r in rows})
    if len(seasons) != 2:
        return None
    lo, hi = seasons
    by = {}
    for pid, season, team, games in rows:
        by.setdefault(pid, {})[season] = (team, games or 0)
    eligible = moved = 0
    for seasons_by_team in by.values():
        a, b = seasons_by_team.get(lo), seasons_by_team.get(hi)
        if not a or not b:
            continue
        (ta, ga), (tb, gb) = a, b
        if ga >= 8 and gb >= 8 and ta and tb:
            eligible += 1
            if ta != tb:
                moved += 1
    return {"from_season": lo, "to_season": hi, "eligible": eligible, "moved": moved,
            "rate": round(moved / eligible, 4) if eligible else None}


def draft_outcomes(rows, started_seasons, through_season):
    """Draft outcomes by round from (player_id, draft_season, round, position, games) rows.

    Only classes with at least two seasons to establish are counted (draft_season <= through_season - 1).
    started_seasons: {player_id: set(seasons)} where a season counts as started when the player held
    depth 1 at his position in at least one week (depth_charts). Returns
    {round: {"n", "games_median", "share_started_2plus"}}.
    """
    by_round = {}
    for pid, draft_season, rnd, _position, games in rows:
        try:
            r = int(rnd)
        except (TypeError, ValueError):
            continue
        if not 1 <= r <= 7 or draft_season > through_season - 1:
            continue
        by_round.setdefault(r, []).append({"games": games or 0,
                                           "started": len(started_seasons.get(pid) or ())})
    out = {}
    for r, cohort in sorted(by_round.items()):
        games = [c["games"] for c in cohort]
        out[str(r)] = {"n": len(cohort), "games_median": D.band(games).get("median"),
                       "share_started_2plus": D.share_at_least([c["started"] for c in cohort], 2)}
    return out


def rookie_usage(rows):
    """First-season games of drafted skill players from (player_id, draft_season, round, position, games)."""
    games = []
    for _pid, _draft_season, _round, position, g in rows:
        grp = mu_group_of(position)
        if grp is None or grp == "OL":
            continue
        games.append(g or 0)
    if not games:
        return {"n": 0}
    return {"n": len(games), "median_games": D.band(games).get("median"),
            "share_8plus": D.share_at_least(games, 8), "share_12plus": D.share_at_least(games, 12)}


# ------------------------------------------------------------------ database reads

def _fetch(cur, sql, params=None):
    cur.execute(sql, params or ())
    return cur.fetchall()


def table_exists(cur, name):
    return bool(_fetch(cur, "SELECT to_regclass(%s) IS NOT NULL", (name,))[0][0])


def read_drafted_ids(cur):
    """Players Madden records as drafted: players.draft_round 1-7 (PLYR_DRAFTROUND, reader v9 / load_save).
    Veterans get it from the save, so the cohort is not limited to players Mu drafted (§7 decision 10)."""
    return {r[0] for r in _fetch(cur, "SELECT id FROM players WHERE draft_round BETWEEN 1 AND 7")}


def read_season_summary(cur, season):
    """The row counts that say whether the season has enough data for the gates at all."""
    def one(sql, params=()):
        rows = _fetch(cur, sql, params)
        return rows[0][0] if rows else 0
    completed = one("SELECT count(*) FROM games WHERE season = %s AND status = 'COMPLETED'", (season,))
    regular_runs = one("SELECT count(DISTINCT week) FROM league_office_runs "
                       "WHERE season = %s AND stage = 'REGULAR_WEEK' "
                       "AND status IN ('WRITTEN', 'PLANNED')", (season,))
    return {
        "completed_games": completed,
        "regular_weeks_run": regular_runs,
        "player_season_rows": one("SELECT count(*) FROM player_seasons WHERE season = %s", (season,)),
        "retirements": one("SELECT count(*) FROM player_retirements WHERE season <= %s", (season,)),
        "transitions": (one("SELECT count(*) FROM player_season_transitions WHERE to_season = %s", (season,))
                        if table_exists(cur, "player_season_transitions") else 0),
        "proposals": one("SELECT count(*) FROM gm_trade_proposals WHERE season = %s", (season,)),
        "picks_numbered": (one("SELECT count(*) FROM draft_picks "
                               "WHERE draft_season = %s AND overall_pick IS NOT NULL", (season + 1,))
                           if table_exists(cur, "draft_picks") else 0),
        "prospects_linked": (one("SELECT count(*) FROM draft_prospects "
                                 "WHERE player_id IS NOT NULL AND actual_pick IS NOT NULL")
                             if table_exists(cur, "draft_prospects") else 0),
    }


def read_games_played(cur, seasons):
    """[(player_id, season, team_id, games)] for the seasons given; season_stat_general first, else the
    distinct games in player_stats."""
    if not seasons:
        return []
    return _fetch(cur, """
        SELECT ps.player_id, ps.season, ps.team_id,
               COALESCE(g.games_played, pg.games, 0) AS games
          FROM player_seasons ps
          LEFT JOIN season_stat_general g
                 ON g.player_id = ps.player_id AND g.season = ps.season
          LEFT JOIN (SELECT pst.player_id, gm.season, count(DISTINCT pst.game_id) AS games
                       FROM player_stats pst JOIN games gm ON gm.id = pst.game_id
                      GROUP BY 1, 2) pg
                 ON pg.player_id = ps.player_id AND pg.season = ps.season
         WHERE ps.season = ANY(%s)""", (list(seasons),))


def read_started_seasons(cur, season):
    """{player_id: set(seasons)} for players who held depth 1 at their position in a week."""
    out = {}
    for pid, s in _fetch(cur, "SELECT DISTINCT player_id, season FROM depth_charts "
                              "WHERE depth = 1 AND player_id IS NOT NULL AND season <= %s", (season,)):
        out.setdefault(pid, set()).add(s)
    return out


def read_draft_rows(cur, season):
    """[(player_id, draft_season, round, position, games)] for linked actual picks with a round 1-7."""
    if not table_exists(cur, "draft_prospects"):
        return []
    rows = _fetch(cur, """
        SELECT dp.player_id, dp.draft_season, dp.actual_round, dp.position,
               COALESCE(g.games_played, pg.games, 0) AS games
          FROM draft_prospects dp
          LEFT JOIN season_stat_general g
                 ON g.player_id = dp.player_id AND g.season = dp.draft_season
          LEFT JOIN (SELECT pst.player_id, gm.season, count(DISTINCT pst.game_id) AS games
                       FROM player_stats pst JOIN games gm ON gm.id = pst.game_id
                      GROUP BY 1, 2) pg
                 ON pg.player_id = dp.player_id AND pg.season = dp.draft_season
         WHERE dp.player_id IS NOT NULL AND dp.actual_pick IS NOT NULL
           AND dp.draft_season <= %s""", (season,))
    return [r for r in rows if str(r[2] or "").strip() in {str(i) for i in range(1, 8)}]


def read_career_rows(cur, season):
    """[(player_id, retired_season, row_season, position, yoe)] for retirements up to `season`."""
    return _fetch(cur, """
        SELECT r.player_id, r.season, ps.season, ps.position, ps.years_of_experience
          FROM player_retirements r
          JOIN player_seasons ps ON ps.player_id = r.player_id
         WHERE r.season <= %s
         ORDER BY r.player_id, ps.season""", (season,))


def read_retirement_rows(cur, season):
    """[(player_id, retired_season, position, age)] for retirements up to `season`."""
    return _fetch(cur, """
        SELECT r.player_id, r.season, ps.position, ps.age
          FROM player_retirements r
          LEFT JOIN player_seasons ps ON ps.player_id = r.player_id AND ps.season = r.season
         WHERE r.season <= %s""", (season,))


def _movement_pair(cur, season):
    """(from_season, to_season) for team movement, or None. Prefers season -> season+1 once the next season
    has games; otherwise falls back to season-1 -> season (the churn that produced this roster)."""
    counts = dict(_fetch(cur, "SELECT season, count(*) FROM games "
                              "WHERE status = 'COMPLETED' AND season IN (%s, %s) GROUP BY 1",
                         (season - 1, season + 1)))
    if counts.get(season + 1):
        return season, season + 1
    if counts.get(season - 1):
        return season - 1, season
    return None


def extract(cur, season, league_current_season=None):
    """Every gate's observed value for `season`, as a plain JSON-able dict. Nothing is written."""
    drafted_ids = read_drafted_ids(cur)
    tenures = _fetch(cur, "SELECT team_id, coach_id, start_season, end_season FROM coach_tenures")
    proposals = _fetch(cur, "SELECT season, week, status, trade_window FROM gm_trade_proposals "
                            "WHERE season = %s", (season,))
    summary = read_season_summary(cur, season)
    draft_rows = read_draft_rows(cur, season)
    started = read_started_seasons(cur, season) if draft_rows else {}

    pair = _movement_pair(cur, season)
    movement = movement_counts(read_games_played(cur, pair)) if pair else None

    return {
        "season": season,
        "meta": {
            "extracted_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "league_current_season": league_current_season,
            "drafted_cohort": len(drafted_ids),
            "movement_pair": pair,
        },
        "coach_changes": coach_changes(tenures, season),
        "trades": trade_counts(proposals, season, last_week=_last_week(cur, season)),
        "career_length": career_lengths(read_career_rows(cur, season), drafted_ids),
        "retirement_age": retirement_ages(read_retirement_rows(cur, season), drafted_ids),
        "team_movement": movement,
        "draft_by_round": draft_outcomes(draft_rows, started, season),
        "rookie_usage": rookie_usage([r for r in draft_rows if r[1] <= season]),
        "data": summary,
    }


def _last_week(cur, season):
    rows = _fetch(cur, "SELECT max(week) FROM games WHERE season = %s", (season,))
    return int(rows[0][0] or REGULAR_WEEKS) if rows else REGULAR_WEEKS


# ------------------------------------------------------------------ scoring

@dataclass
class Gate:
    code: str
    title: str
    observed: object
    target: str
    verdict: str
    detail: str = ""

    def as_dict(self):
        return dict(self.__dict__)


@dataclass
class DecisionReport:
    season: int
    gates: list = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    reference: dict = field(default_factory=dict)
    overall: str = INCOMPLETE

    def counts(self):
        c = {PASS: 0, FAIL: 0, NOT_MEASURED: 0, INCOMPLETE: 0, REPORT_ONLY: 0}
        for g in self.gates:
            c[g.verdict] = c.get(g.verdict, 0) + 1
        return c

    def as_dict(self):
        return {"season": self.season, "overall": self.overall, "counts": self.counts(),
                "meta": self.meta, "gates": [g.as_dict() for g in self.gates], "reference": self.reference}


def _median_gate(code, title, obs, target_median, tol, missing_detail):
    if obs is None or "median" not in obs:
        return Gate(code, title, "—", f"{target_median:g} ± {tol:g}", NOT_MEASURED, missing_detail)
    if obs.get("n", 0) < MIN_COHORT:
        return Gate(code, title, f"{obs['median']:.1f} (n={obs['n']})", f"{target_median:g} ± {tol:g}",
                    NOT_MEASURED, f"cohort n={obs['n']} is below the {MIN_COHORT} minimum")
    ok = abs(obs["median"] - target_median) <= tol + 1e-9
    return Gate(code, title, f"{obs['median']:.1f} (n={obs['n']})", f"{target_median:g} ± {tol:g}",
                PASS if ok else FAIL, "")


def _band_gate(code, title, value, lo, hi, detail="", unit=""):
    if value is None:
        return Gate(code, title, "—", f"{lo}–{hi}", NOT_MEASURED, detail)
    ok = lo <= value <= hi
    return Gate(code, title, f"{value}{unit}", f"{lo}–{hi}", PASS if ok else FAIL, detail)


def _pct(value):
    return "—" if value is None else f"{value:.0%}"


def _share_gate(code, title, obs, target_share, detail=""):
    if obs is None:
        return Gate(code, title, "—", f"{target_share:.0%} ± {SHARE_TOL:.0%}", NOT_MEASURED, detail)
    ok = abs(obs - target_share) <= SHARE_TOL + 1e-9
    return Gate(code, title, f"{obs:.0%}", f"{target_share:.0%} ± {SHARE_TOL:.0%}",
                PASS if ok else FAIL, detail)


def compare_decisions(observed, targets):
    """Score one season's observed decisions against the 2015-2025 targets. Pure."""
    season = observed["season"]
    gates = []
    meta = dict(observed.get("meta") or {})
    meta["target_seasons"] = (targets.get("meta") or {}).get("seasons")
    meta["data"] = observed.get("data") or {}

    # A1 career length, drafted cohort, per group (§2 A1 / §7 decision 2)
    cl = observed["career_length"]
    drafted_cl = cl.get("drafted") or {}
    cohort_n = meta.get("drafted_cohort", 0)
    missing = ("no player has a Madden draft round (players.draft_round 1-7: reload a save with reader v9 so "
               "Madden's PLYR_DRAFTROUND reaches the database), so the drafted cohort is empty" if not cohort_n
               else f"no drafted career has ended in this database (cohort n={cohort_n})")
    for grp in D.GROUP_ORDER:
        tgt = ((targets.get("career_length") or {}).get("drafted") or {}).get(grp) or {}
        if "median" not in tgt:
            continue
        tol = MEDIAN_TOL_LONG if grp in LONG_MEDIAN_GROUPS else MEDIAN_TOL
        gates.append(_median_gate(f"A1.{grp}", f"Career length, {grp} (seasons)", drafted_cl.get(grp),
                                  tgt["median"], tol, missing))

    # A2 retirement age, drafted cohort, per group
    ra = observed["retirement_age"]
    drafted_ra = ra.get("drafted") or {}
    for grp in D.GROUP_ORDER:
        tgt = ((targets.get("retirement_age") or {}).get("drafted") or {}).get(grp) or {}
        if "median" not in tgt:
            continue
        gates.append(_median_gate(f"A2.{grp}", f"Retirement age, {grp}", drafted_ra.get(grp),
                                  tgt["median"], AGE_TOL, missing))

    # A3 head-coach changes
    cc = observed["coach_changes"]
    tcc = (targets.get("coach_changes") or {})
    if not cc.get("teams"):
        for key, title in (("between_seasons", "HC changes between seasons"),
                           ("midseason", "Teams with a mid-season HC change")):
            gates.append(Gate(f"A3.{key}", title, "—", "—", NOT_MEASURED, "no head-coach tenures"))
    else:
        b = tcc.get("between_seasons") or {}
        gates.append(_band_gate("A3.between_seasons", "HC changes between seasons", cc["between_seasons"],
                                b.get("lo"), b.get("hi"),
                                f"hires taking effect in {season + 1} (coach_tenures starting then)"))
        m = tcc.get("midseason") or {}
        gates.append(_band_gate("A3.midseason", "Teams with a mid-season HC change", cc["midseason"],
                                m.get("lo"), m.get("hi"),
                                "Mu fires at Black Monday / playoff exit, so 0 is expected unless an "
                                "in-season firing is added"))

    # A4 trades — gated only when the weekly league office actually ran
    tr = observed["trades"]
    ttr = (targets.get("trades") or {}).get("in_season") or {}
    weeks = (observed.get("data") or {}).get("regular_weeks_run", 0)
    rule_out = "no single season above 30 or below 3"
    if weeks < REGULAR_WEEKS:
        detail = (f"the weekly league office ran {weeks} of {REGULAR_WEEKS} regular-season weeks, so the "
                  "trade engine never saw the whole season (season_loop sims the season offline; a weekly "
                  "shadow season is required)")
        gates.append(Gate("A4.in_season", "In-season trades (Week 1 → season end)", "—",
                          f"{ttr.get('lo')}–{ttr.get('hi')}", NOT_MEASURED, detail))
    else:
        gates.append(_band_gate("A4.in_season", "In-season trades (Week 1 → season end)", tr["in_season"],
                                ttr.get("lo"), ttr.get("hi"), f"also {rule_out}"))
    toff = (targets.get("trades") or {}).get("offseason") or {}
    gates.append(Gate("A4.offseason", "Offseason trades (reference)", tr["offseason"],
                      f"{toff.get('lo')}–{toff.get('hi')}", REPORT_ONLY,
                      "offseason stages only; the A4 gate is the in-season count"))

    # A5 players changing teams
    mv = observed.get("team_movement")
    tmv = (targets.get("team_movement") or {}).get("rate") or {}
    if not mv or mv.get("rate") is None:
        gates.append(Gate("A5", "Established players changing teams", "—", "±20% of the real median",
                          NOT_MEASURED,
                          "no consecutive simulated seasons with >= 8 games both sides "
                          "(player_season_transitions MOVED, else player_seasons)"))
    else:
        median = tmv.get("median") or 0.249
        lo, hi = median * (1 - RATE_TOL), median * (1 + RATE_TOL)
        ok = lo <= mv["rate"] <= hi
        gates.append(Gate("A5", "Established players changing teams",
                          f"{mv['rate']:.1%} ({mv['moved']}/{mv['eligible']}, {mv['from_season']}→"
                          f"{mv['to_season']})",
                          f"{lo:.1%}–{hi:.1%} (±{RATE_TOL:.0%} of {median:.1%})",
                          PASS if ok else FAIL, ""))

    # A6 draft by round
    dbr = observed["draft_by_round"]
    tdbr = targets.get("draft_by_round") or {}
    measured = []
    for r in range(1, 8):
        tgt = (tdbr.get(str(r)) or {}).get("share_started_2plus")
        obs = (dbr.get(str(r)) or {})
        if tgt is None:
            continue
        if "share_started_2plus" not in obs or obs.get("n", 0) < MIN_COHORT:
            gates.append(Gate(f"A6.round{r}", f"Draft round {r}: 2+ started seasons", "—",
                              f"{tgt:.0%} ± {SHARE_TOL:.0%}", NOT_MEASURED,
                              "no linked drafted class with 2+ seasons in this database"))
            continue
        gate = _share_gate(f"A6.round{r}", f"Draft round {r}: 2+ started seasons",
                           obs["share_started_2plus"], tgt, f"n={obs['n']}")
        gates.append(gate)
        measured.append((r, obs["share_started_2plus"]))
    if len(measured) >= 2:
        drops = all(measured[i][1] >= measured[i + 1][1] - 1e-9 for i in range(len(measured) - 1))
        gates.append(Gate("A6.monotonic", "Draft rounds decline monotonically",
                          " → ".join(f"R{r} {v:.0%}" for r, v in measured), "non-increasing",
                          PASS if drops else FAIL, ""))
    else:
        gates.append(Gate("A6.monotonic", "Draft rounds decline monotonically", "—", "non-increasing",
                          NOT_MEASURED, "fewer than two measured rounds"))

    # A7 rookie usage
    ru = observed["rookie_usage"]
    tru = targets.get("rookie_usage") or {}
    if ru.get("n", 0) < MIN_COHORT:
        for key, title, target in (
                ("median_games", "Rookie games (median)",
                 f"{tru.get('median_games')} ± {ROOKIE_MEDIAN_TOL:g}"),
                ("share_8plus", "Rookies with 8+ games",
                 f"{_pct(tru.get('share_8plus'))} ± {SHARE_TOL:.0%}"),
                ("share_12plus", "Rookies with 12+ games",
                 f"{_pct(tru.get('share_12plus'))} ± {SHARE_TOL:.0%}")):
            gates.append(Gate(f"A7.{key}", title, "—", target, NOT_MEASURED,
                              "no linked drafted skill rookies in this database"))
    else:
        gates.append(Gate("A7.median_games", "Rookie games (median)", f"{ru['median_games']:g}",
                          f"{tru.get('median_games')} ± {ROOKIE_MEDIAN_TOL:g}",
                          PASS if abs(ru["median_games"] - tru["median_games"]) <= ROOKIE_MEDIAN_TOL
                          else FAIL, f"n={ru['n']}"))
        gates.append(_share_gate("A7.share_8plus", "Rookies with 8+ games", ru["share_8plus"],
                                 tru.get("share_8plus"), f"n={ru['n']}"))
        gates.append(_share_gate("A7.share_12plus", "Rookies with 12+ games", ru["share_12plus"],
                                 tru.get("share_12plus"), f"n={ru['n']}"))

    verdicts = {g.verdict for g in gates}
    if FAIL in verdicts:
        overall = FAIL
    elif NOT_MEASURED in verdicts or INCOMPLETE in verdicts:
        overall = INCOMPLETE
    else:
        overall = PASS

    reference = {
        "career_length_all_players": cl.get("all_players") or {},
        "retirement_age_all_players": ra.get("all_players") or {},
        "careers_measured": cl.get("careers"),
        "retirements_measured": ra.get("n"),
        "all_trades": tr.get("all"),
        "proposals": tr.get("proposed"),
        "proposals_by_status": tr.get("by_status") or {},
    }
    return DecisionReport(season=season, gates=gates, meta=meta, reference=reference, overall=overall)


# ------------------------------------------------------------------ report

def _mark(verdict):
    return {PASS: "✓", FAIL: "✗", NOT_MEASURED: "—", INCOMPLETE: "◐", REPORT_ONLY: "·"}.get(verdict, "?")


def format_report(report):
    seasons = report.meta.get("target_seasons") or []
    band = f"{seasons[0]}–{seasons[-1]}" if seasons else "target"
    c = report.counts()
    lines = [f"# Decision comparison: season {report.season}", ""]
    lines.append(f"- Real-NFL targets: {band} (`engines/tuning/decision_targets.py`). "
                 f"Gates: `docs/reference/TUNING_ACCEPTANCE_THRESHOLDS.md` §1–§2.")
    lines.append(f"- **Verdict: {report.overall}** — {c[PASS]} pass, {c[FAIL]} fail, "
                 f"{c[NOT_MEASURED]} not measured.")
    if report.overall == INCOMPLETE:
        lines.append("- INCOMPLETE is not a pass: a gate with no data in Mu's output is a failure of coverage "
                     "(`TUNING_ACCEPTANCE_THRESHOLDS.md` §1, no silent pass). The reasons are below.")
    lines += ["", "## Gates (§2, decided)", "",
              "| gate | observed | real target | verdict |", "|---|---|---|---|"]
    for g in report.gates:
        lines.append(f"| {g.code} · {g.title} | {g.observed} | {g.target} | {_mark(g.verdict)} {g.verdict} |")

    reasons = [g for g in report.gates if g.verdict in (NOT_MEASURED, INCOMPLETE) and g.detail]
    if reasons:
        lines += ["", "## Not measured, and why", ""]
        lines += [f"- **{g.code}** {g.title}: {g.detail}" for g in reasons]

    ref = report.reference
    if ref.get("career_length_all_players"):
        lines += ["", "## Reference, not gated (all-players cohort)", "",
                  "The decided career-length and retirement-age gates use the **drafted** cohort. The "
                  "all-players column is the published floor (`TUNING_ACCEPTANCE_THRESHOLDS.md` §0.2).", "",
                  "| group | career length (median, n) | retirement age (median, n) |", "|---|---:|---:|"]
        all_cl = ref.get("career_length_all_players") or {}
        all_ra = ref.get("retirement_age_all_players") or {}
        for grp in D.GROUP_ORDER:
            a, b = all_cl.get(grp) or {}, all_ra.get(grp) or {}
            if "median" not in a and "median" not in b:
                continue
            ca = f"{a['median']:.1f} ({a['n']})" if "median" in a else "—"
            cb = f"{b['median']:.1f} ({b['n']})" if "median" in b else "—"
            lines.append(f"| {grp} | {ca} | {cb} |")

    d = report.meta.get("data") or {}
    if d:
        lines += ["", "## What the run held", "",
                  "| item | count |", "|---|---:|"]
        for key, label in (("completed_games", "completed games in the season"),
                           ("regular_weeks_run", "regular-season weeks the league office ran"),
                           ("player_season_rows", "player_seasons rows for the season"),
                           ("retirements", "retirements up to the season"),
                           ("transitions", "player_season_transitions rows for the next season"),
                           ("proposals", "gm_trade_proposals rows for the season"),
                           ("picks_numbered", "coming-draft picks with an overall number"),
                           ("prospects_linked", "draft prospects linked to a player")):
            if key in d:
                lines.append(f"| {label} | {d[key]:,} |")
        if d.get("regular_weeks_run", 0) < REGULAR_WEEKS:
            lines += ["", "A tuning season run by `season_loop` sims the whole season in Madden and ingests "
                      "only at the playoffs and the offseason stops, so the weekly decision engines (trades "
                      "above all) never run week by week. The in-season gates need a **weekly shadow season** "
                      "(P5), not a `season_loop` run."]

    lines += ["", "## Not yet computable (`TUNING_ACCEPTANCE_THRESHOLDS.md` §4)", "",
              "Listed, not faked:", "", "| decision | why it matters | how to get it |", "|---|---|---|"]
    lines += [f"| {a} | {b} | {c} |" for a, b, c in NOT_YET_COMPUTABLE]

    lines += ["", "## Reported, not gated (§5)", "",
              "These look like targets but cannot tell you which engine to fix; they are reported only.", "",
              "| quantity | why it is not a gate |", "|---|---|"]
    lines += [f"| {a} | {b} |" for a, b in REPORTED_NOT_GATED]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ CLI

def default_out_dir(dsn):
    """data/tuning/seasons/<run>/ for a madden_tune_<run> database, else data/tuning."""
    try:
        from psycopg.conninfo import conninfo_to_dict
        db = (conninfo_to_dict(dsn).get("dbname") or "").lower()
    except Exception:                                   # noqa: BLE001 - a malformed DSN belongs to connect
        db = ""
    if db.startswith("madden_tune_"):
        return os.path.join(SEASONS_DIR, db[len("madden_tune_"):])
    return os.path.join(ROOT, "data", "tuning")


def run(dsn, season, *, targets_path=None, out_dir=None, connect=None, write=True):
    """Extract, score and (by default) write decisions_<season>.md/.json. Returns the written paths and report."""
    targets_path = targets_path or TARGETS_DEFAULT
    if not os.path.exists(targets_path):
        raise FileNotFoundError(f"no decision targets at {targets_path}: build them with "
                                "python3 -m engines.tuning.decision_targets")
    with open(targets_path, encoding="utf-8") as fh:
        targets = json.load(fh)
    connect = connect or connect_read_only
    with connect(dsn) as conn:
        cur = conn.cursor()
        rows = _fetch(cur, "SELECT current_season FROM league_state WHERE id = 1")
        league_season = rows[0][0] if rows else None
        observed = extract(cur, season, league_season)
    report = compare_decisions(observed, targets)
    out = {"report": report, "observed": observed, "paths": {}}
    if write:
        out_dir = out_dir or default_out_dir(dsn)
        os.makedirs(out_dir, exist_ok=True)
        md_path = os.path.join(out_dir, f"decisions_{season}.md")
        json_path = os.path.join(out_dir, f"decisions_{season}.json")
        with open(md_path, "w", encoding="utf-8") as fh:
            fh.write(format_report(report))
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump({"report": report.as_dict(), "observed": observed}, fh, indent=1, default=str)
        out["paths"] = {"md": md_path, "json": json_path}
    return out


def connect_read_only(dsn):
    """A read-only session: the report may be pointed at any database and never writes to it."""
    import psycopg
    return psycopg.connect(dsn, options="-c default_transaction_read_only=on")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Score a tuning season's decisions against the real-NFL targets.")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL"),
                    help="a tuning database, e.g. host=localhost dbname=madden_tune_p2g user=postgres "
                         "password=postgres")
    ap.add_argument("--season", type=int, required=True, help="the season just played (a league year)")
    ap.add_argument("--targets", default=TARGETS_DEFAULT, help="decision targets JSON")
    ap.add_argument("--out-dir", help="default: beside the stat report for a madden_tune_* database")
    ap.add_argument("--json", action="store_true", help="print the JSON report instead of the markdown")
    ap.add_argument("--no-write", action="store_true", help="print only; write no files")
    args = ap.parse_args(argv)
    if not args.dsn:
        print("no --dsn and no DATABASE_URL", file=sys.stderr)
        return 2
    try:
        out = run(args.dsn, args.season, targets_path=args.targets, out_dir=args.out_dir,
                  write=not args.no_write)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 2
    except Exception as e:                          # noqa: BLE001 - a DSN that won't connect is a clean error
        try:
            import psycopg
            if isinstance(e, psycopg.Error):
                print(f"cannot read {args.dsn}: {e}", file=sys.stderr)
                return 3
        except ImportError:
            pass
        raise
    if args.json:
        print(json.dumps({"report": out["report"].as_dict(), "observed": out["observed"]},
                         indent=1, default=str))
    else:
        print(format_report(out["report"]), end="")
    if out["paths"]:
        print(f"\nwrote {out['paths']['md']}\nwrote {out['paths']['json']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
