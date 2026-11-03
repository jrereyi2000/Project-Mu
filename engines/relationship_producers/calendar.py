"""
Relationship producer calendar (RELATIONSHIP_EVENT_PRODUCERS_PLAN.md §2.1). Pure: no database.

A tick is the league_state week. Weeks 1-18 are the regular season, 19-23 the playoffs (22 the Pro Bowl), and
24-32 the offseason stops (pipeline.league_office.STAGE_WEEKS: OFFSEASON_START 24 ... PRESEASON 32), all counted
in the season just played (engines/contracts_engine.py: a season's offseason comes after its week 23). So every
season has TICKS_PER_SEASON = 32 ticks, the relationships plan's calendar input.

  tick_for(snapshot, league_state_week) -> tick     in season: the league_state week; offseason: STAGE_WEEKS
  calendar(seasons) -> {season: 32}
  time_point(season, tick) -> {"season", "tick"}
  advance(season, tick, n) -> time point n ticks later (rolling into the next season)
"""

TICKS_PER_SEASON = 32
LAST_GAME_TICK = 23                     # the last playoff week; ticks after it are offseason stops
FIRST_OFFSEASON_TICK = LAST_GAME_TICK + 1


def _check_tick(tick):
    if isinstance(tick, bool) or not isinstance(tick, int) or not 1 <= tick <= TICKS_PER_SEASON:
        raise ValueError(f"tick must be an int 1-{TICKS_PER_SEASON}, got {tick!r}")
    return tick


def _check_season(season):
    if isinstance(season, bool) or not isinstance(season, int):
        raise ValueError(f"season must be an int, got {season!r}")
    return season


def is_in_season(tick):
    """True for game weeks (1-23), False for offseason stops (24-32)."""
    return _check_tick(tick) <= LAST_GAME_TICK


def tick_for(snapshot, league_state_week):
    """The tick of a reader snapshot: the league_state week after the load in season (falling back to
    league_office.league_week when it is None), else league_office.STAGE_WEEKS of its stage.

    pipeline.league_office is imported here, not at module level, so importing calendar.py needs no database."""
    from pipeline import league_office as lo

    stage = lo.stage_and_wave(snapshot)[0]
    if stage in lo.IN_SEASON:
        week = league_state_week if league_state_week is not None else lo.league_week(snapshot, stage)
        if not 1 <= int(week) <= LAST_GAME_TICK:
            raise ValueError(f"in-season week {week!r} outside 1-{LAST_GAME_TICK}")
        return int(week)
    return _check_tick(lo.STAGE_WEEKS[stage])


def calendar(seasons):
    """{season: TICKS_PER_SEASON} for every season given (the relationships plan's calendar input)."""
    return {_check_season(s): TICKS_PER_SEASON for s in sorted(set(seasons))}


def time_point(season, tick):
    """{"season": season, "tick": tick}, validated."""
    return {"season": _check_season(season), "tick": _check_tick(tick)}


def advance(season, tick, n=1):
    """The time point n ticks (n >= 0) after (season, tick), rolling past tick 32 into the next season."""
    _check_season(season)
    _check_tick(tick)
    if n < 0:
        raise ValueError("n must be >= 0")
    index = (tick - 1) + int(n)
    return time_point(season + index // TICKS_PER_SEASON, index % TICKS_PER_SEASON + 1)
