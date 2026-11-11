"""EA Madden 27 ratings (NFL_WEEKLY_MIRROR_PLAN.md §2.8). Pure parsing plus one cached download.

EA's public ratings page (`https://www.ea.com/games/madden-nfl/ratings?page=N`) serves the newest weekly Madden 27
iteration in its Next.js ``__NEXT_DATA__`` blob: ``props.pageProps.ratingDetails`` holds 100 players per page and a
``totalItems`` count.  ``fetch_iteration`` walks pages until one adds no new ids, waits 1.5 s between pages, and
caches the merged item list at ``data/history/.cache/ea_m27_<iteration>.json`` with its sha256.  The iteration id
(``madden-ratings-week-8``) is read from the page, never assumed, and every later page and item must report that
same iteration: a refresh that meets another week's page refuses instead of caching two iterations under one id,
and a failed refresh leaves an existing cache file untouched (it is written to a temporary file and only then
swapped in).

The rest is pure and testable from ``tests/fixtures/ea_ratings_page.json``:

- ``index(items)`` keys each item by ``normalize_name`` plus birthdate;
- ``find(real, idx)`` matches a ``RealPlayer`` on name and birthdate, or on name alone when EA has no birthdate
  and the position group agrees (§2.8, R4b);
- ``madden_fields(item)`` maps EA's 55 camelCase stat keys through ``EA_TO_SAVE`` to the save's ``<Name>Rating``
  fields, and EA's position ids (``LEDG``, ``MIKE``, ...) through ``pipeline.load_save.POSITIONS`` reversed.

An EA key with no save field (or a value we cannot write as a number, e.g. ``runningStyle``'s enum name) is listed
under ``unmapped`` in ``madden_fields``; it is never guessed.
"""
import glob
import hashlib
import json
import logging
import os
import re
import time
import urllib.request

from engines.nfl_history.fetch import FetchedFile
from engines.nfl_history.link import normalize_name, position_ok
from pipeline.load_save import POSITIONS

log = logging.getLogger(__name__)

BASE_URL = "https://www.ea.com/games/madden-nfl/ratings"
USER_AGENT = "project-mu-nfl-mirror"          # generic client name, as engines.nfl_history.fetch uses
PAGE_DELAY_S = 1.5
TIMEOUT_S = 60
CACHE_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "history", ".cache"))
CACHE_PREFIX = "ea_m27_"
MIN_STATS = 50
REQUIRED_STATS = ("speed", "overall", "awareness")

# The save's Player fields for EA's 55 stat keys.  EA's `overall` is OverallRating; `runningStyle` names an enum
# and is listed as unmapped rather than converted.  The save also has LongSnap and ThrowAccuracy, which EA does
# not publish: they keep the donor record's values (never guessed).
EA_TO_SAVE = {
    "acceleration": "AccelerationRating",
    "agility": "AgilityRating",
    "awareness": "AwarenessRating",
    "bCVision": "BCVisionRating",
    "blockShedding": "BlockSheddingRating",
    "breakSack": "BreakSackRating",
    "breakTackle": "BreakTackleRating",
    "carrying": "CarryingRating",
    "catchInTraffic": "CatchInTrafficRating",
    "catching": "CatchingRating",
    "changeOfDirection": "ChangeOfDirectionRating",
    "deepRouteRunning": "DeepRouteRunningRating",
    "finesseMoves": "FinesseMovesRating",
    "hitPower": "HitPowerRating",
    "impactBlocking": "ImpactBlockingRating",
    "injury": "InjuryRating",
    "jukeMove": "JukeMoveRating",
    "jumping": "JumpingRating",
    "kickAccuracy": "KickAccuracyRating",
    "kickPower": "KickPowerRating",
    "kickReturn": "KickReturnRating",
    "leadBlock": "LeadBlockRating",
    "manCoverage": "ManCoverageRating",
    "mediumRouteRunning": "MediumRouteRunningRating",
    "overall": "OverallRating",
    "passBlock": "PassBlockRating",
    "passBlockFinesse": "PassBlockFinesseRating",
    "passBlockPower": "PassBlockPowerRating",
    "playAction": "PlayActionRating",
    "playRecognition": "PlayRecognitionRating",
    "powerMoves": "PowerMovesRating",
    "press": "PressRating",
    "pursuit": "PursuitRating",
    "release": "ReleaseRating",
    "runBlock": "RunBlockRating",
    "runBlockFinesse": "RunBlockFinesseRating",
    "runBlockPower": "RunBlockPowerRating",
    "runningStyle": "RunningStyleRating",
    "shortRouteRunning": "ShortRouteRunningRating",
    "spectacularCatch": "SpectacularCatchRating",
    "speed": "SpeedRating",
    "spinMove": "SpinMoveRating",
    "stamina": "StaminaRating",
    "stiffArm": "StiffArmRating",
    "strength": "StrengthRating",
    "tackle": "TackleRating",
    "throwAccuracyDeep": "ThrowAccuracyDeepRating",
    "throwAccuracyMid": "ThrowAccuracyMidRating",
    "throwAccuracyShort": "ThrowAccuracyShortRating",
    "throwOnTheRun": "ThrowOnTheRunRating",
    "throwPower": "ThrowPowerRating",
    "throwUnderPressure": "ThrowUnderPressureRating",
    "toughness": "ToughnessRating",
    "trucking": "TruckingRating",
    "zoneCoverage": "ZoneCoverageRating",
}
SAVE_POSITION = {madden: mu for mu, madden in POSITIONS.items()}   # LEDG -> LE, MIKE -> MLB, SAM -> LOLB, ...

_urlopen = urllib.request.urlopen   # monkeypatched in tests


# ----------------------------------------------------------------------------------------------- fetch ----
def cache_path(name):
    return os.path.join(CACHE_DIR, name)


def cache_name(iteration):
    """`ea_m27_<iteration>.json`, with anything but a safe file-name character folded to '-'."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", str(iteration)).strip("-")
    return f"{CACHE_PREFIX}{safe}.json"


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _fetched(path):
    return FetchedFile(path, BASE_URL, os.path.basename(path), _sha256(path), os.path.getsize(path))


def _newest_cached():
    paths = glob.glob(cache_path(CACHE_PREFIX + "*.json"))
    if not paths:
        return None
    return _fetched(max(paths, key=os.path.getmtime))


def _page_url(page):
    return f"{BASE_URL}?page={int(page)}"


def _fetch_page(page):
    request = urllib.request.Request(_page_url(page), headers={"User-Agent": USER_AGENT})
    with _urlopen(request, timeout=TIMEOUT_S) as response:
        return response.read().decode("utf-8", "replace")


def parse_page(html):
    """{"iteration", "total", "items"} from one page's ``__NEXT_DATA__``; fails loudly on a shape change (R4a)."""
    match = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html or "", re.S)
    if not match:
        raise RuntimeError("EA ratings page has no __NEXT_DATA__ (shape changed?)")
    try:
        data = json.loads(match.group(1))
        details = data["props"]["pageProps"]["ratingDetails"]
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError("EA ratings page has no props.pageProps.ratingDetails (shape changed?)") from exc
    items = details.get("items")
    if not isinstance(items, list):
        raise RuntimeError("EA ratings page has no ratingDetails.items list (shape changed?)")
    return {"iteration": _iteration(items), "total": details.get("totalItems"), "items": items}


def _item_iteration(item):
    """One EA item's iteration id: ``iteration`` is either the id or a ``{"id", "label"}`` object."""
    iteration = item.get("iteration") if isinstance(item, dict) else None
    if isinstance(iteration, dict):
        iteration = iteration.get("id")
    return str(iteration).strip() if iteration else None


def _iteration(items):
    for item in items or ():
        iteration = _item_iteration(item)
        if iteration:
            return iteration
    return None


def _validate_iteration(items, iteration, page):
    """Refuse a page carrying another iteration's items (audit M-0509 F1).

    EA can publish a new weekly iteration between two page requests, and a later page's players would otherwise
    be cached under the first page's id, permanently rating created players from a mixed-week source.  Every
    readable item iteration must agree with the refresh's iteration; an item with no iteration id cannot disagree.
    """
    other = sorted({_item_iteration(item) for item in items or ()} - {None, iteration})
    if other:
        raise RuntimeError(
            f"EA ratings page {page} carries iteration {', '.join(other)} but the refresh is {iteration}; "
            "refusing to cache two iterations under one id")


def _validate_page(parsed, page):
    if page != 1:
        return
    if not parsed["iteration"]:
        raise RuntimeError("EA ratings page has no iteration id")
    total = parsed["total"]
    if not isinstance(total, int) or total <= 0:
        raise RuntimeError(f"EA ratings page totalItems is {total!r}")
    items = parsed["items"]
    if not items:
        raise RuntimeError("EA ratings page 1 has no players")
    stats = items[0].get("stats")
    if not isinstance(stats, dict) or len(stats) < MIN_STATS:
        raise RuntimeError(f"EA ratings page stats changed: {len(stats) if isinstance(stats, dict) else 0} keys")
    missing = [key for key in REQUIRED_STATS if key not in stats]
    if missing:
        raise RuntimeError(f"EA ratings page stats changed: missing {', '.join(missing)}")


def fetch_iteration(refresh=False):
    """The newest EA Madden 27 ratings iteration as a cached FetchedFile.

    With `refresh` false an existing cache is returned without a request.  Otherwise pages are read until one adds
    no new player id (or `totalItems` is reached), and the merged list is cached as
    `data/history/.cache/ea_m27_<iteration>.json`.  Every page and item must carry the first page's iteration; a
    page from another iteration refuses the refresh, and because the cache is written last (to a temporary file,
    then replaced) a failed refresh never damages an existing cache file.
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    if not refresh:
        cached = _newest_cached()
        if cached is not None:
            return cached

    items, seen, iteration, total, page = [], set(), None, None, 1
    while True:
        if page > 1:
            time.sleep(PAGE_DELAY_S)
        parsed = parse_page(_fetch_page(page))
        _validate_page(parsed, page)
        if page == 1:
            iteration, total = parsed["iteration"], parsed["total"]
        _validate_iteration(parsed["items"], iteration, page)
        new = [item for item in parsed["items"] if _item_id(item) not in seen]
        if not new:
            break
        for item in new:
            seen.add(_item_id(item))
            items.append(item)
        if total is not None and len(items) >= total:
            break
        page += 1

    path = cache_path(cache_name(iteration))
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"iteration": iteration, "total_items": total, "items": items}, fh,
                  ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)
    log.info("EA Madden 27 ratings %s: %d players cached at %s", iteration, len(items), path)
    return _fetched(path)


def read_payload(fetched):
    """The cached JSON object for a FetchedFile (or a path)."""
    path = str(getattr(fetched, "path", fetched))
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def read_items(fetched):
    """The cached EA item list for a FetchedFile (or a path)."""
    payload = read_payload(fetched)
    return payload.get("items", []) if isinstance(payload, dict) else payload


def _item_id(item):
    return item.get("id") or (_full_name(item), item.get("birthdate"))


# ----------------------------------------------------------------------------------------------- index ----
def _full_name(item):
    name = " ".join(str(item.get(key) or "").strip() for key in ("firstName", "lastName")).strip()
    return name or str(item.get("fullName") or item.get("name") or "").strip()


def _date(value):
    """'YYYY-MM-DD' from EA's 'YYYY-MM-DD 00:00:00' (or a real player's date), else None."""
    text = str(value or "").strip()
    return text[:10] if re.match(r"\d{4}-\d{2}-\d{2}", text) else None


def index(items):
    """{keyed by normalized name and birthdate: item} for every EA player."""
    out = {}
    for item in items or ():
        out.setdefault((normalize_name(_full_name(item)), _date(item.get("birthdate"))), item)
    return out


def find(real, idx):
    """The EA item for a RealPlayer: name and birthdate, or name alone when EA has no birthdate (R4b).

    The name-only fallback also requires the position group to agree, and returns None when several EA players
    share the name.  A birthdate mismatch never falls back; the caller uses the template instead.
    """
    name = normalize_name(getattr(real, "name", None))
    if not name:
        return None
    birthdate = _date(getattr(real, "birth_date", None))
    if birthdate:
        hit = idx.get((name, birthdate))
        if hit is not None:
            return hit
    candidates = [item for (item_name, _), item in idx.items() if item_name == name]
    loose = [item for item in candidates if _date(item.get("birthdate")) is None
             and _position_agrees(item, real)]
    return loose[0] if len(loose) == 1 else None


def _position_agrees(item, real):
    position = ea_position(item)
    real_position = getattr(real, "position", None)
    if not position or not real_position:
        return False
    return position_ok(position, real_position)


# ----------------------------------------------------------------------------------------------- fields ----
def ea_position(item):
    """The save's position code for an EA item: LEDG -> LE, MIKE -> MLB, SAM -> LOLB, WILL -> ROLB, else unchanged."""
    position = item.get("position")
    if isinstance(position, dict):
        position = position.get("id")
    position = str(position or "").strip()
    return SAVE_POSITION.get(position, position)


def ea_archetype(item):
    archetype = item.get("archetype")
    if isinstance(archetype, dict):
        archetype = archetype.get("id")
    return str(archetype).strip() if archetype else None


def madden_fields(item):
    """{"position", "archetype", "ratings": {SaveField: value}, "unmapped": [EA key, ...]} for one EA item.

    Only numeric EA stats with a save field are written.  `unmapped` lists keys absent from EA_TO_SAVE and mapped
    keys whose value is not a number (EA's `runningStyle` is an enum name), so the report can show them.
    """
    ratings, unmapped = {}, []
    for key, raw in (item.get("stats") or {}).items():
        field = EA_TO_SAVE.get(key)
        value = raw.get("value") if isinstance(raw, dict) else raw
        if field is None:
            unmapped.append(key)
        elif isinstance(value, bool) or not isinstance(value, (int, float)):
            unmapped.append(key)
        else:
            ratings[field] = int(value)
    return {"position": ea_position(item), "archetype": ea_archetype(item), "ratings": ratings,
            "unmapped": sorted(unmapped)}
