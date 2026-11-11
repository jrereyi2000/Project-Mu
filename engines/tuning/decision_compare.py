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
  A1 career length         every career that had ended by the season: a retirement (player_retirements), or
                           two consecutive seasons with no signed-or-played player_seasons row and no later
                           signing, which reopens it (TUNING_ACCEPTANCE_THRESHOLDS.md §7 decision 11). A season
                           is signed-or-played when its row has a team_id (Signed/PracticeSquad) or the player
                           has games that season (season_stat_general, else player_stats): an offseason
                           FreeAgent capture keeps the same season number and nulls a played season's team, so
                           team_id alone is not enough. A retiree with no signed-or-played season still counts,
                           with the retirement-season row as the snapshot. Length is years_of_experience + 1
                           over those seasons, else their distinct count; the drafted cohort from
                           players.draft_round 1-7 (Madden's PLYR_DRAFTROUND, §7 decision 10), not
                           draft_class/draft_picks
  A2 retirement age        the age on the last signed-or-played season of that same cohort, or on the
                           retirement-season row for a retiree with neither (a retirement may be recorded a
                           year after it)
  A3 head-coach changes    coach_tenures (head coaches only; coordinator_tenures is a different role); the
                           between-seasons count is measured only when that season's hiring cycle ran
                           (coach_hot_seat rows exist)
  A4 trades                gm_trade_proposals APPROVED/EXECUTED; the in-season gate also needs the weekly
                           league office to have run (league_office_runs REGULAR_WEEK WRITTEN), else the engine
                           never saw weeks 2-18 and the count is not a season's worth of decisions
  A5 team movement         player_seasons team_id across two consecutive seasons, with >= 8 games each
                           (season_stat_general, else player_stats games); the ledger's MOVED rows are not read
  A6 draft by round        draft_prospects actual picks linked to players; "started season" = 8+ games
                           started at the player's own position — depth 1 in his own slot, in a week his team
                           played, in the chart in force for that team/week (the newest week <= that week,
                           each slot JR > MU_COACH > MADDEN, §7 decision 12), not a week in any slot
  A7 rookie usage          first-season games of drafted skill players, excluding linemen (no stat line)

Per-season count gates (A3 between seasons, A4 in-season trades, A5 team movement) are scored on a season's
three consecutive-season mean, with each of those seasons also inside its band (TUNING_ACCEPTANCE_THRESHOLDS.md
§1 "3-season average" and §7 decision 4). A season counts only once it has been simulated (MIN_SEASON_GAMES).

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
WINDOW_SEASONS = 3                        # §1/§7 decision 4: a season's count is judged on a 3-season mean
MIN_SEASON_GAMES = 256                    # 32 x 17 / 2 = 272 regular games; 256 proves the season was played
MIN_MOVEMENT_PLAYERS = 300                # A5: below this an eligibility cohort is noise (the real one is ~780)
A4_SEASON_LO, A4_SEASON_HI = 3, 25        # §2 A4 / §7 decision 9: a single in-season trade count can wander this far
MEDIAN_TOL = 1.0                          # §7 decision 3: median +/-1 season
MEDIAN_TOL_LONG = 1.5                     # ... +/-1.5 for QB/K/P
AGE_TOL = 1.5                             # A2: median +/-1.5 years
SHARE_TOL = 0.10                          # shares +/-10 points
RATE_TOL = 0.20                           # rates +/-20% relative
ROOKIE_MEDIAN_TOL = 2.0                   # A7: median games +/-2
LONG_MEDIAN_GROUPS = ("QB", "K", "P")
STARTED_SEASON_WEEKS = 8                  # §7 decision 12: a started season is 8+ games started at his position
STARTED_SOURCES = ("JR", "MU_COACH", "MADDEN")   # per slot JR > MU_COACH > MADDEN; MADDEN is the fallback
UNSIGNED_SEASONS_END_CAREER = 2           # §7 decision 11: two consecutive seasons with no signed-or-played row end it

# Madden 27 positions -> decision_targets' position groups (D.group_of handles the nflverse spellings)
MU_GROUPS = {
    "QB": {"QB"}, "RB": {"HB", "FB", "RB"}, "WR": {"WR"}, "TE": {"TE"},
    "OL": {"LT", "LG", "C", "RG", "RT", "OT", "OG", "T", "G", "OL"},
    "DL": {"LE", "RE", "DT", "NT", "LEDG", "REDG", "DE", "DL"},
    "LB": {"LOLB", "MLB", "ROLB", "MIKE", "SAM", "WILL", "OLB", "ILB", "LB"},
    "DB": {"CB", "FS", "SS", "DB", "S"},
    "K": {"K"}, "P": {"P"},
}
# Madden's roster position -> the depth-chart slot that is that position. The defensive codes the roster uses
# (LEDG/REDG/MIKE/WILL/SAM) and RB map to the slot's name; every other position is its own slot name.
OWN_SLOT = {"LEDG": "LE", "REDG": "RE", "MIKE": "MLB", "WILL": "ROLB", "SAM": "LOLB", "RB": "HB"}
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
                       the season's Black Monday and hiring cycle made — plus teams whose coach's tenure ends
                       in `season` with no successor hired yet (an unfilled vacancy is still a change)
      midseason        teams with two or more distinct head coaches inside `season`
      teams            teams with a head coach in `season`
      vacancies        teams counted above whose successor had not been hired (a hiring cycle still running)
    """
    def covering(s):
        by_team = {}
        for team, coach, start, end in tenures:
            if start <= s and (end is None or end >= s):
                by_team.setdefault(team, []).append((start, coach, end))
        out = {}
        for t, rows in by_team.items():
            rows.sort()
            first, last = rows[0][1], rows[-1][1]
            out[t] = (first, last, rows[-1][2])
        return out

    cur, nxt = covering(season), covering(season + 1)
    between = vacancies = 0
    for t, (_first, last, last_end) in cur.items():
        if t in nxt:
            if last != nxt[t][0]:
                between += 1
        elif last_end == season:            # the coach left and no successor has been hired yet
            between += 1
            vacancies += 1
    mid = sum(1 for first, last, _end in cur.values() if first != last)
    return {"between_seasons": between, "midseason": mid, "teams": len(cur), "vacancies": vacancies}


def trade_counts(rows, season, last_week=REGULAR_WEEKS):
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


def started_seasons(rows):
    """{player_id: set(seasons)} for seasons the player started at his own position.

    rows: (player_id, season, week, position_slot, own_slot, source). A season counts only when he was depth 1
    at his own slot in STARTED_SEASON_WEEKS (8) distinct weeks of the chart in force (TUNING_ACCEPTANCE_
    THRESHOLDS.md §7 decision 12): the reader resolves each slot JR > MU_COACH > MADDEN, so all three sources
    are eligible here. A returner or sub-package slot (KR, PR, 3DRB, SLWR, SUBLB, ...) is not his own
    position and never counts.
    """
    weeks = {}
    for pid, season, week, slot, own_slot, source in rows:
        if source not in STARTED_SOURCES or not own_slot or slot != own_slot:
            continue
        weeks.setdefault((pid, season), set()).add(week)
    out = {}
    for (pid, season), season_weeks in weeks.items():
        if len(season_weeks) >= STARTED_SEASON_WEEKS:
            out.setdefault(pid, set()).add(season)
    return out


def draft_outcomes(rows, started_seasons, through_season):
    """Draft outcomes by round from (player_id, draft_season, round, position, games) rows.

    Only classes with at least two seasons to establish are counted (draft_season <= through_season - 1).
    started_seasons: {player_id: set(seasons)} from started_seasons(): a season counts as started only when
    the player held his own position in 8+ weeks (depth_charts). Returns
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
                       "AND status = 'WRITTEN'", (season,))
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


def read_season_facts(cur, seasons):
    """{season: {"completed_games", "regular_weeks_run"}} for the seasons asked for.

    A season is *simulated* when it has MIN_SEASON_GAMES completed games; the weekly engine has run a season
    when it has REGULAR_WEEKS DISTINCT REGULAR_WEEK runs with status WRITTEN (PLANNED is only the pre-write row).
    """
    seasons = sorted(set(seasons))
    if not seasons:
        return {}
    games = dict(_fetch(cur, "SELECT season, count(*) FROM games "
                              "WHERE status = 'COMPLETED' AND season = ANY(%s) GROUP BY 1", (seasons,)))
    weeks = dict(_fetch(cur, "SELECT season, count(DISTINCT week) FROM league_office_runs "
                             "WHERE stage = 'REGULAR_WEEK' AND status = 'WRITTEN' "
                             "AND season = ANY(%s) GROUP BY 1", (seasons,)))
    return {s: {"completed_games": int(games.get(s) or 0),
                "regular_weeks_run": int(weeks.get(s) or 0)} for s in seasons}


def read_hiring_cycle(cur, seasons):
    """{season: {"hot_seat_rows", "hc_hires"}} — the evidence that a season's hiring cycle ran.

    `coach_hot_seat` holds one row per team once Black Monday has decided (fired true or false), so any rows
    for a season prove the cycle ran; `staff_moves` HC HIRED rows are the hires it produced. A season with no
    hot-seat rows (--no-offseason, or a walk that stopped before the offseason) cannot be scored.
    """
    seasons = sorted(set(seasons))
    out = {s: {"hot_seat_rows": 0, "hc_hires": 0} for s in seasons}
    if not seasons:
        return out
    if table_exists(cur, "coach_hot_seat"):
        for s, n in _fetch(cur, "SELECT season, count(*) FROM coach_hot_seat "
                                "WHERE season = ANY(%s) GROUP BY 1", (seasons,)):
            out.setdefault(s, {"hot_seat_rows": 0, "hc_hires": 0})["hot_seat_rows"] = int(n)
    if table_exists(cur, "staff_moves"):
        for s, n in _fetch(cur, "SELECT season, count(*) FROM staff_moves "
                                "WHERE role = 'HC' AND action = 'HIRED' AND season = ANY(%s) GROUP BY 1",
                           (seasons,)):
            out.setdefault(s, {"hot_seat_rows": 0, "hc_hires": 0})["hc_hires"] = int(n)
    return out


def read_trade_rows(cur, seasons):
    """[(season, week, status, trade_window)] for the seasons given."""
    if not seasons:
        return []
    return _fetch(cur, "SELECT season, week, status, trade_window FROM gm_trade_proposals "
                       "WHERE season = ANY(%s)", (sorted(seasons),))


def read_last_weeks(cur, seasons):
    """{season: last game week} so a playoff-week trade still counts as in-season."""
    if not seasons:
        return {}
    return {s: int(w or REGULAR_WEEKS) for s, w in
            _fetch(cur, "SELECT season, max(week) FROM games WHERE season = ANY(%s) GROUP BY 1",
                   (sorted(seasons),))}


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
    """{player_id: set(seasons)} for seasons the player started at his own position (8+ games, chart in force).

    The chart in force carries forward: for each completed regular-season team-week, the newest stored chart
    week at or before it is read, and each slot at that week resolves JR > MU_COACH > MADDEN, so a NULL or
    absent player at one source falls through to the next (JR's ruling, 2026-11-10/11). The fallback holds
    within one chart week: a slot that no source charts at the week in force has no starter that week. Charts
    stored under the season with week 31 or 32 — that year's cutdown and preseason, which record_written
    files under the calendar year — sort before regular-season week 1; weeks 19-30 (postseason and
    offseason) never reach a regular-season week. Every season through the report season is read in one
    call, because A6 counts started seasons across a career, not only in the report season. Only depth 1 is
    read. A week
    counts only when his team played a regular-season game that week: a start is a game (nflverse
    seasons_started), so a bye week's chart is not one. The player's own slot comes from his position that
    season (player_seasons), mapped with OWN_SLOT; depth 1 in a situational or returner slot is a week on
    that unit, not a start at his position. MADDEN is the chart read back from the save, the fallback when Mu
    has not set the slot.
    """
    rows = _fetch(cur, """
        WITH grid AS (
            SELECT DISTINCT g.season, g.week, t.team_id
              FROM games g
              CROSS JOIN LATERAL (VALUES (g.home_team_id), (g.away_team_id)) t(team_id)
             WHERE g.status = 'COMPLETED' AND g.game_type = 'REGULAR_SEASON' AND g.season <= %s
        ), chart_weeks AS (
            SELECT DISTINCT team_id, season, week,
                   CASE WHEN week IN (31, 32) THEN week - 100 ELSE week END AS ord
              FROM depth_charts
             WHERE season <= %s AND source IN ('JR', 'MU_COACH', 'MADDEN')
        ), in_force AS (
            SELECT gr.team_id, gr.season, gr.week, cw.week AS chart_week
              FROM grid gr
              JOIN LATERAL (
                  SELECT c.week
                    FROM chart_weeks c
                   WHERE c.team_id = gr.team_id AND c.season = gr.season AND c.ord <= gr.week
                   ORDER BY c.ord DESC
                   LIMIT 1
              ) cw ON true
        ), slot1 AS (
            SELECT DISTINCT ON (f.team_id, f.season, f.week, dc.position_slot)
                   f.team_id, f.season, f.week, dc.position_slot, dc.player_id, dc.source
              FROM in_force f
              JOIN depth_charts dc
                ON dc.team_id = f.team_id AND dc.season = f.season AND dc.week = f.chart_week
             WHERE dc.depth = 1 AND dc.player_id IS NOT NULL
             ORDER BY f.team_id, f.season, f.week, dc.position_slot,
                      CASE dc.source WHEN 'JR' THEN 0 WHEN 'MU_COACH' THEN 1 ELSE 2 END
        )
        SELECT s.player_id, s.season, s.week, s.position_slot,
               CASE UPPER(ps.position) WHEN 'LEDG' THEN 'LE' WHEN 'REDG' THEN 'RE'
                    WHEN 'MIKE' THEN 'MLB' WHEN 'WILL' THEN 'ROLB' WHEN 'SAM' THEN 'LOLB'
                    WHEN 'RB' THEN 'HB' ELSE UPPER(ps.position) END AS own_slot,
               s.source
          FROM slot1 s
          JOIN player_seasons ps ON ps.player_id = s.player_id AND ps.season = s.season""",
                 (season, season))
    return started_seasons(rows)


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


# A career ends at a retirement, or after UNSIGNED_SEASONS_END_CAREER consecutive seasons with no signed-or-
# played player_seasons row and no later signing — a later signing reopens it (TUNING_ACCEPTANCE_THRESHOLDS.md
# §7 decision 11). A season is signed-or-played when its row has a team_id (Signed/PracticeSquad) or the player
# has games that season (season_stat_general, else player_stats): the offseason ingests keep the same season
# number, so a FreeAgent capture nulls a played season's team (pipeline/load_save.py; JR 2026-11-11). `ended`
# holds exactly one row per player: the endpoint is the retirement season when there is one (preferred over the
# unsigned inference) and the last signed-or-played season otherwise, while `last_season` is always the latest
# signed-or-played season at or before that endpoint, the common position/age snapshot both readers share — a
# retiree with no signed-or-played season at all keeps the retirement season as that snapshot row (JR
# 2026-11-11). A retirement keeps its own season only while no signed row follows it, and a signing after it
# reopens the career. Its second parameter is the cutoff `season - UNSIGNED_SEASONS_END_CAREER`.
_ENDED_CAREERS = """
WITH signed_seasons AS (
    SELECT ps.player_id, ps.season
      FROM player_seasons ps
      LEFT JOIN season_stat_general g
             ON g.player_id = ps.player_id AND g.season = ps.season
      LEFT JOIN (SELECT pst.player_id, gm.season, count(DISTINCT pst.game_id) AS games
                   FROM player_stats pst JOIN games gm ON gm.id = pst.game_id
                  GROUP BY 1, 2) pg
             ON pg.player_id = ps.player_id AND pg.season = ps.season
     WHERE ps.player_id IS NOT NULL
       AND (ps.team_id IS NOT NULL OR COALESCE(g.games_played, pg.games, 0) > 0)
     GROUP BY ps.player_id, ps.season
), last_active AS (
    SELECT player_id, max(season) AS last_season
      FROM signed_seasons
     GROUP BY player_id
), retired AS (
    SELECT player_id, max(season) AS end_season
      FROM player_retirements
     WHERE season <= %s AND player_id IS NOT NULL
     GROUP BY player_id
), ended AS (
    SELECT COALESCE(r.player_id, la.player_id) AS player_id,
           CASE WHEN r.end_season IS NOT NULL
                     AND COALESCE(la.last_season, r.end_season) <= r.end_season
                THEN r.end_season ELSE la.last_season END AS end_season,
           COALESCE(la.last_season, r.end_season) AS last_season
      FROM retired r
      FULL OUTER JOIN last_active la ON la.player_id = r.player_id
     WHERE (r.end_season IS NOT NULL
            AND COALESCE(la.last_season, r.end_season) <= r.end_season)
        OR (la.last_season IS NOT NULL AND la.last_season <= %s)
)
"""


def read_career_rows(cur, season):
    """[(player_id, end_season, row_season, position, yoe)] for every career ended by `season`.

    The population is retirements plus unsigned free agents (two consecutive seasons with no signed-or-played
    roster row), which is what the real A1/A2 cohort is; a signing after the gap reopens the career, so his
    last active season moves with it. `end_season` is the endpoint the career ended at (a retirement year can be
    later than the last signed-or-played season); `row_season` is normally a signed-or-played season at or
    before it, so a FreeAgent year with no team and no games cannot add years of experience or a position,
    while a played season whose team an offseason ingest nulled is kept; a retiree with neither falls back to
    his retirement-season row.
    """
    return _fetch(cur, _ENDED_CAREERS + """
        SELECT e.player_id, e.end_season, ps.season, ps.position, ps.years_of_experience
          FROM ended e
          JOIN signed_seasons ss ON ss.player_id = e.player_id AND ss.season <= e.end_season
          JOIN player_seasons ps ON ps.player_id = ss.player_id AND ps.season = ss.season
        UNION ALL
        SELECT e.player_id, e.end_season, ps.season, ps.position, ps.years_of_experience
          FROM ended e
          JOIN player_seasons ps ON ps.player_id = e.player_id AND ps.season = e.last_season
         WHERE NOT EXISTS (SELECT 1 FROM signed_seasons ss
                            WHERE ss.player_id = e.player_id AND ss.season <= e.end_season)
         ORDER BY 1, 3""",
                  (season, season - UNSIGNED_SEASONS_END_CAREER))


def read_retirement_rows(cur, season):
    """[(player_id, end_season, position, age)] for every career ended by `season` (the same population).

    Exactly one row per ended player: position and age come from his latest signed-or-played season (the
    shared `last_season`), not from the endpoint year, so a retirement recorded a year after the last roster
    snapshot still reports that snapshot's age. A retiree with no signed-or-played season at all is snapshotted
    from his retirement-season row (JR 2026-11-11).
    """
    return _fetch(cur, _ENDED_CAREERS + """
        SELECT e.player_id, e.end_season, ps.position, ps.age
          FROM ended e
          JOIN player_seasons ps ON ps.player_id = e.player_id AND ps.season = e.last_season""",
                  (season, season - UNSIGNED_SEASONS_END_CAREER))


def extract(cur, season, league_current_season=None):
    """Every gate's observed value for `season`, as a plain JSON-able dict. Nothing is written.

    The per-season count gates read three consecutive seasons: `season - 2 … season` for A3/A4, and the three
    movement pairs `season - 3 → season - 2 … season - 1 → season` for A5. Each entry carries the facts that
    say whether that season can be scored at all (completed games, weekly league office runs, hot-seat rows).
    """
    drafted_ids = read_drafted_ids(cur)
    tenures = _fetch(cur, "SELECT team_id, coach_id, start_season, end_season FROM coach_tenures")
    window = [season - WINDOW_SEASONS + 1 + i for i in range(WINDOW_SEASONS)]   # season-2, season-1, season
    movement_seasons = [season - WINDOW_SEASONS + i for i in range(WINDOW_SEASONS + 1)]
    facts = read_season_facts(cur, movement_seasons)
    hiring = read_hiring_cycle(cur, window)
    proposals = read_trade_rows(cur, window)
    last_weeks = read_last_weeks(cur, window)
    summary = read_season_summary(cur, season)
    draft_rows = read_draft_rows(cur, season)
    started = read_started_seasons(cur, season) if draft_rows else {}

    cc_by_season = {}
    for s in window:
        entry = coach_changes(tenures, s)
        entry.update(hiring.get(s) or {})
        entry.update(facts.get(s) or {})
        cc_by_season[s] = entry

    trades_by_season = {}
    for s in window:
        entry = trade_counts(proposals, s, last_week=last_weeks.get(s, REGULAR_WEEKS))
        entry.update(facts.get(s) or {})
        trades_by_season[s] = entry

    movement_rows = read_games_played(cur, movement_seasons)
    movement_pairs = []
    for lo, hi in zip(movement_seasons, movement_seasons[1:]):
        mv = movement_counts([r for r in movement_rows if r[1] in (lo, hi)])
        if mv:
            movement_pairs.append(mv)
    movement = movement_pairs[-1] if movement_pairs else None

    return {
        "season": season,
        "meta": {
            "extracted_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "league_current_season": league_current_season,
            "drafted_cohort": len(drafted_ids),
            "movement_pair": ((movement["from_season"], movement["to_season"]) if movement else None),
        },
        "coach_changes": dict(cc_by_season[season], by_season=cc_by_season),
        "trades": dict(trades_by_season[season], by_season=trades_by_season),
        "career_length": career_lengths(read_career_rows(cur, season), drafted_ids),
        "retirement_age": retirement_ages(read_retirement_rows(cur, season), drafted_ids),
        "team_movement": movement,
        "movement_pairs": movement_pairs,
        "draft_by_round": draft_outcomes(draft_rows, started, season),
        "rookie_usage": rookie_usage([r for r in draft_rows if r[1] <= season]),
        "data": summary,
    }


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


def _pct(value):
    return "—" if value is None else f"{value:.0%}"


def _share_gate(code, title, obs, target_share, detail=""):
    if obs is None:
        return Gate(code, title, "—", f"{target_share:.0%} ± {SHARE_TOL:.0%}", NOT_MEASURED, detail)
    ok = abs(obs - target_share) <= SHARE_TOL + 1e-9
    return Gate(code, title, f"{obs:.0%}", f"{target_share:.0%} ± {SHARE_TOL:.0%}",
                PASS if ok else FAIL, detail)


def _series_key(key):
    return "→".join(str(part) for part in key) if isinstance(key, tuple) else str(key)


def _series_observed(series, fmt):
    """'mean (2024 5, 2025 4, 2026 5)', or '<value> (1 of 3 seasons)' for a partial window."""
    n = len(series)
    mean = sum(series.values()) / n
    if n == 1:
        return f"{fmt(next(iter(series.values())))} (1 of {WINDOW_SEASONS} seasons)"
    parts = ", ".join(f"{_series_key(k)} {fmt(v)}" for k, v in sorted(series.items()))
    if n < WINDOW_SEASONS:
        return f"{fmt(mean)} (mean of {n}; {n} of {WINDOW_SEASONS} seasons: {parts})"
    return f"{fmt(mean)} (mean of {parts})"


def _three_season_gate(code, title, series, per_band, mean_band, target, *,
                       fmt=lambda v: f"{v:g}", missing_detail="", note=""):
    """A per-season count gate scored on a 3-season mean with each season inside its band (§1, §7.4).

    series: {season: value} for the seasons that could be measured, oldest first. PASS only when all
    WINDOW_SEASONS are measured, every one is inside `per_band` and the mean is inside `mean_band`; any
    measured season or mean outside its band is FAIL; a partially measured window is INCOMPLETE, never a pass.
    """
    if not series:
        return Gate(code, title, "—", target, NOT_MEASURED, missing_detail)
    values = list(series.values())
    mean = sum(values) / len(values)
    bad = [(s, v) for s, v in sorted(series.items())
           if not (per_band[0] - 1e-9 <= v <= per_band[1] + 1e-9)]
    if bad:
        verdict = FAIL
        detail = "outside the per-season band: " + ", ".join(f"{_series_key(s)} {fmt(v)}" for s, v in bad)
    elif not (mean_band[0] - 1e-9 <= mean <= mean_band[1] + 1e-9):
        verdict = FAIL
        detail = (f"the {len(values)}-season mean {fmt(mean)} is outside "
                  f"{fmt(mean_band[0])}–{fmt(mean_band[1])}")
    elif len(values) < WINDOW_SEASONS:
        verdict = INCOMPLETE
        detail = (f"{len(values)} of {WINDOW_SEASONS} seasons; the gate needs three consecutive simulated "
                  "seasons")
    else:
        verdict = PASS
        detail = ""
    if note:
        detail = f"{detail}; {note}" if detail else note
    return Gate(code, title, _series_observed(series, fmt), target, verdict, detail)


def compare_decisions(observed, targets):
    """Score a season's observed decisions against the 2015-2025 targets. Pure.

    The per-season count gates read `by_season` / `movement_pairs` from extract(): a season is judged on its
    three consecutive-season mean with each season also inside its band, and a partially measured window is
    INCOMPLETE rather than a pass.
    """
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

    # A3 head-coach changes — three consecutive seasons, each inside the band (§1, §7 decision 4)
    cc = observed["coach_changes"]
    tcc = (targets.get("coach_changes") or {})
    b = tcc.get("between_seasons") or {}
    m = tcc.get("midseason") or {}
    cc_by = cc.get("by_season") or {}
    between_series, mid_series = {}, {}
    for s, entry in cc_by.items():
        if entry.get("completed_games", 0) < MIN_SEASON_GAMES or not entry.get("teams"):
            continue
        mid_series[s] = entry.get("midseason", 0)
        if entry.get("hot_seat_rows"):
            between_series[s] = entry.get("between_seasons", 0)
    no_season = not mid_series
    if no_season:
        missing_cc = (f"none of seasons {min(cc_by) if cc_by else season}-{max(cc_by) if cc_by else season} "
                      f"has a simulated season ({MIN_SEASON_GAMES}+ completed games)")
    elif not between_series:
        missing_cc = ("the hiring cycle has not run for the simulated season(s): no coach_hot_seat rows "
                      "(--no-offseason, or the walk stopped before Black Monday)")
    else:
        missing_cc = ""
    vacancies = sum(entry.get("vacancies", 0) for entry in cc_by.values())
    vac_note = (f"{vacancies} coach change(s) had no successor hired yet and are counted as changes"
                if vacancies else "")
    gates.append(_three_season_gate(
        "A3.between_seasons", "HC changes between seasons", between_series,
        (b.get("lo"), b.get("hi")), (b.get("lo"), b.get("hi")),
        f"{b.get('lo')}–{b.get('hi')} each season and on the 3-season mean",
        missing_detail=missing_cc, note=vac_note))
    # A3.midseason is report-only (JR, 2026-11-10): Mu fires at Black Monday / playoff exit, so 0 is expected
    # until an in-season firing is added; it is shown but never gets a ✓ or ✗.
    if no_season:
        gates.append(Gate("A3.midseason", "Teams with a mid-season HC change", "—",
                          f"{m.get('lo')}–{m.get('hi')} (report only)", REPORT_ONLY,
                          "no simulated season to measure"))
    else:
        gates.append(Gate("A3.midseason", "Teams with a mid-season HC change",
                          _series_observed(mid_series, lambda v: f"{v:g}"),
                          f"{m.get('lo')}–{m.get('hi')} (report only)", REPORT_ONLY,
                          "Mu fires at Black Monday / playoff exit, so 0 is expected unless an in-season "
                          "firing is added"))

    # A4 trades — gated only when the weekly league office actually ran, on three consecutive seasons
    tr = observed["trades"]
    ttr = (targets.get("trades") or {}).get("in_season") or {}
    weeks = (observed.get("data") or {}).get("regular_weeks_run", 0)
    trade_series, no_weekly = {}, []
    for s, entry in (tr.get("by_season") or {}).items():
        if entry.get("completed_games", 0) < MIN_SEASON_GAMES:
            continue
        if entry.get("regular_weeks_run", 0) < REGULAR_WEEKS:
            no_weekly.append(s)
            continue
        trade_series[s] = entry.get("in_season", 0)
    if not trade_series:
        detail = (f"the weekly league office ran {weeks} of {REGULAR_WEEKS} regular-season weeks, so the "
                  "trade engine never saw the whole season (season_loop sims the season offline; a weekly "
                  "shadow season is required)")
        gates.append(Gate("A4.in_season", "In-season trades (Week 1 → season end)", "—",
                          f"{A4_SEASON_LO}–{A4_SEASON_HI} each season; mean {ttr.get('lo')}–{ttr.get('hi')}",
                          NOT_MEASURED, detail))
    else:
        missing_weekly = (f"seasons without {REGULAR_WEEKS} REGULAR_WEEK runs: "
                          + ", ".join(str(s) for s in sorted(no_weekly))) if no_weekly else ""
        gates.append(_three_season_gate(
            "A4.in_season", "In-season trades (Week 1 → season end)", trade_series,
            (A4_SEASON_LO, A4_SEASON_HI), (ttr.get("lo"), ttr.get("hi")),
            f"{A4_SEASON_LO}–{A4_SEASON_HI} each season; mean {ttr.get('lo')}–{ttr.get('hi')}",
            missing_detail="the weekly league office has not run a full regular season",
            note=missing_weekly))
    toff = (targets.get("trades") or {}).get("offseason") or {}
    gates.append(Gate("A4.offseason", "Offseason trades (reference)", tr["offseason"],
                      f"{toff.get('lo')}–{toff.get('hi')}", REPORT_ONLY,
                      f"these are season {season}'s OFFSEASON/DRAFT_DAY rows, the trades made after {season}; "
                      f"the real band applies to the offseason a season follows, so this row belongs beside "
                      f"the real offseason before {season + 1}"))

    # A5 players changing teams — three consecutive season pairs, each inside the published band
    tmv = (targets.get("team_movement") or {}).get("rate") or {}
    median = tmv.get("median") or 0.249
    mean_lo, mean_hi = median * (1 - RATE_TOL), median * (1 + RATE_TOL)
    per_lo, per_hi = tmv.get("lo") or mean_lo, tmv.get("hi") or mean_hi
    movement_series, small_cohorts = {}, []
    for mv in observed.get("movement_pairs") or []:
        if not mv or mv.get("rate") is None:
            continue
        if mv.get("eligible", 0) < MIN_MOVEMENT_PLAYERS:
            small_cohorts.append(f"{mv['from_season']}→{mv['to_season']} n={mv['eligible']}")
            continue
        movement_series[(mv["from_season"], mv["to_season"])] = mv["rate"]
    if not movement_series:
        detail = ("no consecutive simulated seasons with >= 8 games on both sides "
                  "(player_seasons team per season)")
        if small_cohorts:
            detail += (f"; the eligible cohort is below {MIN_MOVEMENT_PLAYERS} players ("
                       + ", ".join(small_cohorts) + ")")
        gates.append(Gate("A5", "Established players changing teams", "—",
                          f"mean ±{RATE_TOL:.0%} of {median:.1%}; each season {per_lo:.1%}–{per_hi:.1%}",
                          NOT_MEASURED, detail))
    else:
        gates.append(_three_season_gate(
            "A5", "Established players changing teams", movement_series,
            (per_lo, per_hi), (mean_lo, mean_hi),
            f"mean within ±{RATE_TOL:.0%} of {median:.1%}; each season {per_lo:.1%}–{per_hi:.1%}",
            fmt=lambda v: f"{v:.1%}",
            missing_detail=(f"the eligible cohort is below {MIN_MOVEMENT_PLAYERS} players"
                            + (f" ({', '.join(small_cohorts)})" if small_cohorts else ""))))

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
                              "no linked drafted class with 2+ seasons in this database (a started season is "
                              f"{STARTED_SEASON_WEEKS}+ games started at the player's own position, in a week "
                              "his team played)"))
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
                 f"{c[INCOMPLETE]} incomplete, {c[NOT_MEASURED]} not measured.")
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
