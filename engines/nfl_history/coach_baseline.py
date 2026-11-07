"""The 2025 coach_seasons baseline (docs/done/REAL_NFL_HISTORY_PLAN.md §2.7; PROJECT_SPEC §3.3h).

A fresh franchise database has no coach_seasons rows, so the season archive would start every head coach from zero
(mu_era_only). This writes one coach_seasons row per head coach with an open tenure for BASELINE_SEASON (2025,
team_id NULL): his real career through 2025 summed from nfl_coach_seasons. A first-time head coach gets a zero row
(a real career of zero, so mu_era_only = false). Must run before the first season archive; apply_baseline refuses,
naming them, if any coach already has a coach_seasons row after 2025 (fix: `season_archive --replace` after it).

- baseline_rows(coaches, nfl_coach_seasons) -> rows (pure); coaches = [{coach_id, coach_name}]
- apply_baseline(cur, rows, all_coaches=None): writes rows, sets nfl_coach_seasons.coach_id and
  nfl_team_seasons.head_coach_id for the names it matches (in the caller's transaction)
- read_inputs(cur) -> (current head coaches, every coach, nfl_coach_seasons rows)
"""
from engines.nfl_history.store import BASELINE_SEASON, write_coach_baseline

COACH_NAME_FIX = {}     # coaches.coach_name -> nflverse spelling, where they differ (none for the 2026 staffs)
SUMS = ("wins", "losses", "ties", "playoff_wins", "playoff_losses", "super_bowl_wins", "super_bowl_losses")


class BaselineRefused(RuntimeError):
    pass


def nfl_name(coach_name):
    return COACH_NAME_FIX.get(coach_name, coach_name)


def baseline_rows(coaches, nfl_coach_seasons):
    by_name = {}
    for r in nfl_coach_seasons:
        if r["season"] <= BASELINE_SEASON:
            by_name.setdefault(r["coach_name"], []).append(r)
    out = []
    for c in sorted(coaches, key=lambda c: c["coach_id"]):
        seasons = by_name.get(nfl_name(c["coach_name"]), [])
        tot = {k: sum(s[k] or 0 for s in seasons) for k in SUMS}
        out.append({
            "coach_id": c["coach_id"], "coach_name": c["coach_name"], "season": BASELINE_SEASON,
            "career_wins": tot["wins"], "career_losses": tot["losses"], "career_ties": tot["ties"],
            "winning_seasons": sum(1 for s in seasons if s["full_season"] and s["wins"] > s["losses"]),
            "playoff_wins": tot["playoff_wins"], "playoff_losses": tot["playoff_losses"],
            "super_bowl_wins": tot["super_bowl_wins"], "super_bowl_losses": tot["super_bowl_losses"],
            "real_seasons": len({s["season"] for s in seasons}), "first_time": not seasons,
        })
    return out


def _rows(cur):
    names = [d[0] for d in cur.description]
    return [r if isinstance(r, dict) else dict(zip(names, r)) for r in cur.fetchall()]


def read_inputs(cur):
    cur.execute("""SELECT DISTINCT c.id AS coach_id, c.coach_name FROM coaches c
                   JOIN coach_tenures t ON t.coach_id = c.id WHERE t.end_season IS NULL ORDER BY c.id""")
    current = _rows(cur)
    cur.execute("SELECT id AS coach_id, coach_name FROM coaches ORDER BY id")
    every = _rows(cur)
    cur.execute("""SELECT coach_name, season, team_id, wins, losses, ties, games, full_season, playoff_wins,
                          playoff_losses, super_bowl_wins, super_bowl_losses FROM nfl_coach_seasons""")
    return current, every, _rows(cur)


def apply_baseline(cur, rows, all_coaches=None):
    ids = [r["coach_id"] for r in rows]
    if ids:
        cur.execute("""SELECT c.coach_name, MIN(s.season) FROM coach_seasons s JOIN coaches c ON c.id = s.coach_id
                       WHERE s.coach_id = ANY(%s) AND s.season > %s GROUP BY c.coach_name ORDER BY 1""",
                    (ids, BASELINE_SEASON))
        later = cur.fetchall()
        if later:
            names = ", ".join(f"{r[0]} ({r[1]})" if not isinstance(r, dict) else str(r) for r in later)
            raise BaselineRefused(f"already archived after {BASELINE_SEASON}: {names}; write the baseline, then "
                                  f"re-run `python3 -m engines.season_archive --replace` for those seasons")
    n = write_coach_baseline(cur, rows)
    for c in all_coaches or [{"coach_id": r["coach_id"], "coach_name": r["coach_name"]} for r in rows]:
        cur.execute("UPDATE nfl_coach_seasons SET coach_id = %s WHERE coach_name = %s",
                    (c["coach_id"], nfl_name(c["coach_name"])))
        cur.execute("UPDATE nfl_team_seasons SET head_coach_id = %s WHERE head_coach = %s",
                    (c["coach_id"], nfl_name(c["coach_name"])))
    return n
