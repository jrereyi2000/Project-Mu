"""Cached downloads for real NFL history (docs/in_progress/REAL_NFL_HISTORY_PLAN.md §2.3). No database.

Files land in data/history/.cache/ (git-ignored, shared with engines.tuning). get() writes to a temporary name and
renames it, so a cut download never caches; it waits POLITE_DELAY_S before each network request so a full fetch
never hammers a host. The client name is the generic USER_AGENT.
"""
import collections
import hashlib
import os
import time
import urllib.request

CACHE_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "history", ".cache"))
USER_AGENT = "project-mu-nfl-history"
TIMEOUT_S = 60
POLITE_DELAY_S = 1.0

NFLVERSE = "https://github.com/nflverse/nflverse-data/releases/download"
NFLDATA_STANDINGS = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/standings.csv"
DEFAULT_SEASONS = range(1999, 2026)
DRAFT_LAST_SEASON = 2026

FetchedFile = collections.namedtuple("FetchedFile", "path url cache_name sha256 bytes")

_urlopen = urllib.request.urlopen   # monkeypatched in tests


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cache_path(cache_name):
    return os.path.join(CACHE_DIR, cache_name)


def get(url, cache_name, refresh=False, delay=POLITE_DELAY_S):
    """The cached file for `url` (downloaded when missing or refresh), with its sha256 and size."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = cache_path(cache_name)
    if refresh or not os.path.exists(path):
        if delay:
            time.sleep(delay)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with _urlopen(req, timeout=TIMEOUT_S) as resp:
            data = resp.read()
        tmp = path + ".part"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    return FetchedFile(path, url, cache_name, _sha256(path), os.path.getsize(path))


def nflverse_name(url):
    return "nflverse_" + url.rsplit("/", 1)[1]


def stats_url(season, season_type):
    kind = {"REG": "reg", "POST": "post"}[season_type]
    return f"{NFLVERSE}/stats_player/stats_player_{kind}_{season}.csv"


def file_list(seasons=DEFAULT_SEASONS):
    """[(url, cache_name)] for every nflverse file a load of `seasons` reads."""
    out = [(f"{NFLVERSE}/players/players.csv", None)]
    for s in seasons:
        out += [(stats_url(s, "REG"), None), (stats_url(s, "POST"), None)]
    out += [(f"{NFLVERSE}/schedules/games.csv", None), (f"{NFLVERSE}/draft_picks/draft_picks.csv", None)]
    out = [(u, nflverse_name(u)) for u, _ in out]
    out.append((NFLDATA_STANDINGS, "nfldata_standings.csv"))
    return out


def fetch_all(seasons=DEFAULT_SEASONS, refresh=False, delay=POLITE_DELAY_S):
    return [get(url, name, refresh, delay) for url, name in file_list(seasons)]


def cached(seasons=DEFAULT_SEASONS):
    """FetchedFile for every file already cached; raises FileNotFoundError naming the missing ones."""
    missing = [name for _, name in file_list(seasons) if not os.path.exists(cache_path(name))]
    if missing:
        raise FileNotFoundError("not cached (run `python3 -m engines.nfl_history fetch`): " + ", ".join(missing[:5])
                                + (f" and {len(missing) - 5} more" if len(missing) > 5 else ""))
    return [get(url, name, False) for url, name in file_list(seasons)]
