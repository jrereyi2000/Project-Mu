"""Plans a team's local beat portfolio for one week (on-demand team wakeups). Pure; no database needed.

Every wakeup covers the whole portfolio, however many beat reporters the team has:
  LOCAL_POSTGAME_REPORT_CARD    if the team played last week
  LOCAL_MATCHUP_PRIMER          if the team plays this week (not on a bye)
  LOCAL_PLAYER_SPOTLIGHT        always
  LOCAL_SEASON_PROGRESS_REPORT  from Week 6
Types the team already has this week (e.g. a matchup primer commissioned by the national desk) are skipped.
Articles are dealt round-robin across the team's reporters, starting at a different reporter each week,
so one reporter doesn't always write the postgame report and a single reporter writes everything.
"""

POSTGAME = "LOCAL_POSTGAME_REPORT_CARD"
PRIMER = "LOCAL_MATCHUP_PRIMER"
SPOTLIGHT = "LOCAL_PLAYER_SPOTLIGHT"
SEASON_PROGRESS = "LOCAL_SEASON_PROGRESS_REPORT"
SEASON_PROGRESS_FIRST_WEEK = 6


def portfolio_types(week: int, played_last_week: bool, plays_this_week: bool, existing_types=()) -> list:
    types = []
    if played_last_week:
        types.append(POSTGAME)
    if plays_this_week:
        types.append(PRIMER)
    types.append(SPOTLIGHT)
    if week >= SEASON_PROGRESS_FIRST_WEEK:
        types.append(SEASON_PROGRESS)
    existing = set(existing_types)
    return [t for t in types if t not in existing]


def plan_local_portfolio(reporter_count: int, week: int, played_last_week: bool, plays_this_week: bool,
                         existing_types=()) -> list:
    """[(reporter_index, article_type), ...] for this week; empty if the team has no beat reporters."""
    if reporter_count <= 0:
        return []
    types = portfolio_types(week, played_last_week, plays_this_week, existing_types)
    return [((week + i) % reporter_count, t) for i, t in enumerate(types)]
