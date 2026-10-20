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


def set_article_context(season: int, week: int, offseason_event: str | None = None):
    """Sets the context for the current thread; returns a token for reset_article_context."""
    ctx = _run_context.get() or {}
    eff_event = offseason_event if offseason_event is not None else ctx.get("offseason_event")
    return _run_context.set({
        "season": int(season) if season is not None else None,
        "week": int(week) if week is not None else None,
        "offseason_event": eff_event,
    })


def reset_article_context(token):
    _run_context.reset(token)


def set_offseason_cutoff(event_name: str | None):
    """Sets or updates the offseason event cutoff for the current thread."""
    ctx = _run_context.get() or {}
    return _run_context.set({
        "season": ctx.get("season"),
        "week": ctx.get("week"),
        "offseason_event": event_name,
    })


def reset_offseason_cutoff(token):
    _run_context.reset(token)


@contextmanager
def article_run_context(season: int, week: int, offseason_event: str | None = None):
    token = set_article_context(season, week, offseason_event=offseason_event)
    try:
        yield
    finally:
        reset_article_context(token)


def context_season():
    ctx = _run_context.get()
    return ctx["season"] if ctx else None


def context_week():
    ctx = _run_context.get()
    return ctx["week"] if ctx else None


def context_offseason_event() -> str | None:
    ctx = _run_context.get()
    return ctx.get("offseason_event") if ctx else None


OFFSEASON_EVENT_ORDER = {
    "super_bowl": 1,
    "coaching": 2,
    "signings": 3,
    "draft": 4,
}


def is_offseason_event_visible(event_name: str) -> bool:
    """True if event_name is visible under the current offseason event cutoff."""
    ctx = _run_context.get()
    if not ctx or not ctx.get("offseason_event"):
        return True
    curr_event = ctx.get("offseason_event")
    curr_order = OFFSEASON_EVENT_ORDER.get(curr_event, 999)
    target_order = OFFSEASON_EVENT_ORDER.get(event_name, 999)
    return target_order <= curr_order


def is_event_visible(event_name: str) -> bool:
    return is_offseason_event_visible(event_name)


def cutoff_week(season: int):
    """First week whose results are hidden for `season`, or None when there is no cutoff."""
    ctx = _run_context.get()
    if not ctx or ctx.get("season") is None or ctx.get("week") is None:
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

