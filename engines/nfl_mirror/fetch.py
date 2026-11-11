"""Cached downloads for the NFL Weekly Mirror (NFL_WEEKLY_MIRROR_PLAN.md §2.2). No database.

fetch_week(season, refresh) fetches the nflverse weekly rosters, injuries, depth charts, schedule (games.csv, the
same cache file engines.nfl_history uses) and OTC contracts through engines.nfl_history.fetch.get, so files land in
data/history/.cache/. read_rows(fetched_file) yields dict rows from a cached CSV or gzipped CSV.
"""
import csv
import gzip
import io

from engines.nfl_history import fetch as history_fetch
from engines.nfl_history.fetch import NFLVERSE, FetchedFile, nflverse_name  # noqa: F401  (FetchedFile re-exported)

DEFAULT_SEASON = 2026
NAMES = ("rosters", "injuries", "depth", "games", "contracts")


def file_list(season=DEFAULT_SEASON):
    """{name: (url, cache_name)} for every file a mirrored week reads."""
    urls = {
        "rosters": f"{NFLVERSE}/weekly_rosters/roster_weekly_{season}.csv.gz",
        "injuries": f"{NFLVERSE}/injuries/injuries_{season}.csv.gz",
        "depth": f"{NFLVERSE}/depth_charts/depth_charts_{season}.csv.gz",
        "games": f"{NFLVERSE}/schedules/games.csv",
        "contracts": f"{NFLVERSE}/contracts/historical_contracts.parquet",
    }
    return {name: (url, nflverse_name(url)) for name, url in urls.items()}


def fetch_week(season=DEFAULT_SEASON, refresh=True, delay=history_fetch.POLITE_DELAY_S):
    """{name: FetchedFile} for rosters, injuries, depth, games and contracts (downloaded when refresh or missing)."""
    return {name: history_fetch.get(url, cache_name, refresh=refresh, delay=delay)
            for name, (url, cache_name) in file_list(season).items()}


def sources(files):
    """{name: sha256} of fetched files, for RealWeek.sources and nfl_mirror_weeks.sources."""
    return {name: f.sha256 for name, f in files.items()}


def read_rows(fetched_file):
    """Yield dict rows from a cached .csv or .csv.gz (a FetchedFile or a path)."""
    path = str(getattr(fetched_file, "path", fetched_file))
    if path.endswith(".parquet"):
        raise ValueError(f"{path} is parquet; read_rows reads CSV only")
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rb") as raw, io.TextIOWrapper(raw, encoding="utf-8", newline="") as text:
        yield from csv.DictReader(text)
