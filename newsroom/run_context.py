"""Season/week of the article currently being written, for the writer research tools (newsroom/db_tools.py).

SwarmDispatcher._execute_agent sets it around each Gemini call. Gemini's automatic function calling runs the
tools on the same thread, so every tool call made while writing an article sees that article's season and
publication week. Each worker thread has its own value, so parallel articles don't interfere.

A Week N article is published before Week N kicks off, so results from Week N onward are hidden: games in those
weeks are treated as not yet played, and newer articles are invisible. This keeps a rerun of an old week from
seeing the future. Season totals stored in aggregate tables (season_stats, team_seasons records, popularity,
ratings) have no per-week history and always reflect the latest data.
"""
import contextvars
from contextlib import contextmanager

_run_context = contextvars.ContextVar("newsroom_run_context", default=None)


def set_article_context(season: int, week: int):
    """Sets the context for the current thread; returns a token for reset_article_context."""
    return _run_context.set({"season": int(season), "week": int(week)})


def reset_article_context(token):
    _run_context.reset(token)


@contextmanager
def article_run_context(season: int, week: int):
    token = set_article_context(season, week)
    try:
        yield
    finally:
        reset_article_context(token)


def context_season():
    ctx = _run_context.get()
    return ctx["season"] if ctx else None


def cutoff_week(season: int):
    """First week whose results are hidden for `season`, or None when there is no cutoff."""
    ctx = _run_context.get()
    if not ctx:
        return None
    if int(season) < ctx["season"]:
        return None            # earlier seasons are complete history
    if int(season) > ctx["season"]:
        return 0               # later seasons: nothing has happened yet
    return ctx["week"]


def is_visible_week(season: int, week: int) -> bool:
    """True if results from (season, week) may be shown to the writer."""
    cut = cutoff_week(season)
    return cut is None or (week is not None and int(week) < cut)
