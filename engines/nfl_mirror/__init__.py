"""NFL Weekly Mirror (docs/done/NFL_WEEKLY_MIRROR_PLAN.md): each 2026 week, make the Madden save match the
real NFL week's rosters, injuries, depth charts and coaches.

fetch.py downloads the nflverse 2026 weekly files (cached, via engines.nfl_history.fetch); real_state.py turns their
rows into a pure RealWeek; trades.py reads JR's hand-kept trade CSV and infers team-to-team moves between weeks.
"""
