"""Build the local coach donor-plate library for Project Mu headshots.

Mirrors ``headshots/local/build_donor_library.py`` (the player donor tool) for
coaching tops instead of jerseys. Source: every club's coach page on the NFL
clubs CDN (``static.clubs.nfl.com``), documented in
``docs/reference/COACH_DONOR_PHOTOS.md`` (Spike D):

* fetch ``/team/coaches/`` and fall back to ``/team/coaches-roster/``;
* extract the transform-stripped **raw upload** (never the UI thumbnail);
* centre-crop to 1024x1024 and save ``data/headshots/coach_donors/{TEAM}_{n}.png``;
* write the matching aperture mask ``{TEAM}_{n}_mask.png`` with the same 2-px
  Gaussian feather as ``donors.generate_tier_mask``;
* register each plate in ``data/headshots/coach_donors/registry.json`` with its
  mask polygon, crop box and provenance.

The club photos are NFL property, personal/non-commercial use only with no
redistribution, so ``data/`` is git-ignored and nothing here is committed.

2-3 plates per team. Weight tiers do not apply: plates vary by build, clothing
and age instead.

Usage:
    python3 -m tools.build_coach_donor_library [--teams CAR,NO] [--no-download]
                                               [--out-dir PATH] [--plates-per-team N]
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import re
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter

from headshots.local.donors import normalize_team

logger = logging.getLogger("build_coach_donor_library")

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_COACH_DONOR_DIR = ROOT / "data" / "headshots" / "coach_donors"

PLATE_SIZE = 1024
MASK_FEATHER_RADIUS = 2.0  # the same 2-px rule as donors.generate_tier_mask

LICENCE = "NFL.com Terms of Use - personal non-commercial only; no redistribution"

# Club domains, checked by Spike D (every club's coach page embeds the CDN).
TEAM_CLUB_DOMAINS: Dict[str, str] = {
    "ARI": "azcardinals.com", "ATL": "atlantafalcons.com", "BAL": "baltimoreravens.com",
    "BUF": "buffalobills.com", "CAR": "panthers.com", "CHI": "chicagobears.com",
    "CIN": "bengals.com", "CLE": "clevelandbrowns.com", "DAL": "dallascowboys.com",
    "DEN": "denverbroncos.com", "DET": "detroitlions.com", "GB": "packers.com",
    "HOU": "houstontexans.com", "IND": "colts.com", "JAX": "jaguars.com",
    "KC": "chiefs.com", "LV": "raiders.com", "LAC": "chargers.com",
    "LAR": "therams.com", "MIA": "miamidolphins.com", "MIN": "vikings.com",
    "NE": "patriots.com", "NO": "neworleanssaints.com", "NYG": "giants.com",
    "NYJ": "newyorkjets.com", "PHI": "philadelphiaeagles.com", "PIT": "steelers.com",
    "SF": "49ers.com", "SEA": "seahawks.com", "TB": "buccaneers.com",
    "TEN": "tennesseetitans.com", "WAS": "commanders.com",
}

TEAM_NAMES: Dict[str, str] = {
    "ARI": "Arizona Cardinals", "ATL": "Atlanta Falcons", "BAL": "Baltimore Ravens",
    "BUF": "Buffalo Bills", "CAR": "Carolina Panthers", "CHI": "Chicago Bears",
    "CIN": "Cincinnati Bengals", "CLE": "Cleveland Browns", "DAL": "Dallas Cowboys",
    "DEN": "Denver Broncos", "DET": "Detroit Lions", "GB": "Green Bay Packers",
    "HOU": "Houston Texans", "IND": "Indianapolis Colts", "JAX": "Jacksonville Jaguars",
    "KC": "Kansas City Chiefs", "LV": "Las Vegas Raiders", "LAC": "Los Angeles Chargers",
    "LAR": "Los Angeles Rams", "MIA": "Miami Dolphins", "MIN": "Minnesota Vikings",
    "NE": "New England Patriots", "NO": "New Orleans Saints", "NYG": "New York Giants",
    "NYJ": "New York Jets", "PHI": "Philadelphia Eagles", "PIT": "Pittsburgh Steelers",
    "SF": "San Francisco 49ers", "SEA": "Seattle Seahawks", "TB": "Tampa Bay Buccaneers",
    "TEN": "Tennessee Titans", "WAS": "Washington Commanders",
}

# Aperture polygons in the 1024x1024 plate space: white = repaint (head, face,
# neck, collar opening), black = keep authentic fabric and the chest logo.
# A coaching top has no shoulder pads and no shield at the collar, and its
# opening is higher and narrower than a jersey's, so these do not reuse
# donors.POLYS_5TIER (which preserve the jersey's metallic NFL shield).
COACH_APERTURE_POLYS: Dict[str, List[Tuple[int, int]]] = {
    "polo": [
        (0, 0), (1024, 0), (1024, 640),
        (640, 710), (610, 820), (575, 880), (540, 925),
        (512, 955),
        (484, 925), (449, 880), (414, 820), (384, 710),
        (0, 640),
    ],
    "quarter_zip": [
        (0, 0), (1024, 0), (1024, 610),
        (630, 675), (600, 780), (568, 850), (536, 905),
        (512, 930),
        (488, 905), (456, 850), (424, 780), (394, 675),
        (0, 610),
    ],
    "jacket": [
        (0, 0), (1024, 0), (1024, 660),
        (655, 730), (615, 845), (575, 905), (540, 945),
        (512, 975),
        (484, 945), (449, 905), (409, 845), (369, 730),
        (0, 660),
    ],
}

COLLAR_KINDS = tuple(COACH_APERTURE_POLYS)
BUILD_CYCLE = ("average", "large", "lean")


# ----------------------------------------------------------------------------
# Pure helpers (no network)
# ----------------------------------------------------------------------------

def build_aperture_mask(collar_kind: str = "polo", feather_radius: float = MASK_FEATHER_RADIUS) -> Image.Image:
    """Return a 1024x1024 'L' aperture mask for a coaching-top collar.

    White (255) is the repaint zone (head/face/neck/collar opening); black (0)
    preserves the authentic fabric and chest logo. The edge is feathered with
    the same 2-px Gaussian blur as ``donors.generate_tier_mask``.
    """
    kind = str(collar_kind).strip().lower()
    poly = COACH_APERTURE_POLYS.get(kind, COACH_APERTURE_POLYS["polo"])
    mask = Image.new("L", (PLATE_SIZE, PLATE_SIZE), 0)
    ImageDraw.Draw(mask).polygon(poly, fill=255)
    if feather_radius > 0:
        mask = mask.filter(ImageFilter.GaussianBlur(radius=feather_radius))
    return mask


def mask_polygon_for(collar_kind: str) -> List[List[int]]:
    """The collar-kind polygon as JSON-friendly ``[[x, y], ...]``."""
    kind = str(collar_kind).strip().lower()
    poly = COACH_APERTURE_POLYS.get(kind, COACH_APERTURE_POLYS["polo"])
    return [[int(x), int(y)] for x, y in poly]


def strip_transform(url: str) -> Optional[str]:
    """Strip the CDN transform segments, returning the raw high-resolution upload.

    ``.../image/upload/t_headshot_desktop/f_auto/lions/abc.jpg`` becomes
    ``.../image/upload/f_auto/lions/abc.jpg``. Handles JSON-escaped (``\\/``)
    and scheme-relative (``//static...``) forms. Returns ``None`` when the URL
    is not a club-CDN image.
    """
    if not url:
        return None
    text = str(url).replace("\\/", "/").replace("\\u002F", "/").strip()
    prefix = "static.clubs.nfl.com/image/"
    idx = text.find(prefix)
    if idx < 0:
        return None
    tail = text[idx + len(prefix):].split("?")[0].split("#")[0]
    segments = [s for s in tail.split("/") if s]
    if len(segments) < 4:
        return None
    kind = segments[0].lower()
    if kind not in ("upload", "private"):
        return None
    fmt_idx = next((i for i, s in enumerate(segments) if s.startswith("f_")), None)
    if fmt_idx is None or fmt_idx + 2 >= len(segments):
        return None
    fmt, club, name = segments[fmt_idx], segments[fmt_idx + 1], segments[fmt_idx + 2]
    return f"https://static.clubs.nfl.com/image/{kind}/{fmt}/{club}/{name}"


_URL_RE = re.compile(r"(?:https?:)?//static\.clubs\.nfl\.com/image/[^\s\"'<>\\]+")


def extract_photo_urls(html: str) -> List[str]:
    """Extract raw, de-duplicated club-CDN image URLs from a coach page."""
    if not html:
        return []
    text = str(html).replace("\\/", "/").replace("\\u002F", "/")
    out: List[str] = []
    for match in _URL_RE.findall(text):
        raw = strip_transform(match)
        if raw and raw not in out:
            out.append(raw)
    return out


def center_crop_plate(image: Image.Image, size: int = PLATE_SIZE) -> Tuple[Image.Image, List[int], List[int]]:
    """Centre-crop a 16:9 studio photo to a square plate and resize to ``size``.

    The crop is biased upward so the head and shoulders stay in frame and more
    of the torso is discarded. Returns ``(plate, crop_box, head_anchor)``.
    """
    im = image.convert("RGBA")
    w, h = im.size
    side = min(w, h)
    left = max(0, (w - side) // 2)
    top = max(0, min(h - side, (h - side) // 3))
    box = (left, top, left + side, top + side)
    plate = im.crop(box).resize((size, size), Image.Resampling.LANCZOS)
    head_anchor = [size // 2, int(size * 0.33)]
    return plate, [int(v) for v in box], head_anchor


def _registry_path(path: Path) -> str:
    """Store a repo-relative path when possible, otherwise an absolute one."""
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return str(path.resolve())


def save_plate(
    out_dir: Path,
    team: str,
    index: int,
    image: Image.Image,
    *,
    collar_kind: str = "polo",
    build: Optional[str] = None,
    clothing: Optional[str] = None,
    headset: bool = False,
    source: str = "team_site",
    source_page: Optional[str] = None,
    source_url: Optional[str] = None,
    attribution: Optional[str] = None,
    credit: Optional[str] = None,
) -> Dict[str, Any]:
    """Normalize, save and describe one coach plate (image + aperture mask)."""
    abbr = normalize_team(team)
    kind = str(collar_kind).strip().lower()
    if kind not in COACH_APERTURE_POLYS:
        raise ValueError(f"Unknown collar_kind {collar_kind!r}; use one of {COLLAR_KINDS}.")

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    plate_id = f"{abbr}_{int(index)}"
    plate_path = out_dir / f"{plate_id}.png"
    mask_path = out_dir / f"{plate_id}_mask.png"

    plate, crop_box, head_anchor = center_crop_plate(image)
    plate.save(plate_path, format="PNG")
    build_aperture_mask(kind).save(mask_path, format="PNG")

    return {
        "id": plate_id,
        "path": _registry_path(plate_path),
        "mask_path": _registry_path(mask_path),
        "mask_polygon": mask_polygon_for(kind),
        "head_anchor": head_anchor,
        "crop_box": crop_box,
        "collar_kind": kind,
        "build": build or BUILD_CYCLE[(int(index) - 1) % len(BUILD_CYCLE)],
        "clothing": clothing or kind,
        "headset": bool(headset),
        "source": source,
        "source_page": source_page,
        "source_url": source_url,
        "licence": LICENCE,
        "attribution": attribution,
        "credit": credit or TEAM_NAMES.get(abbr, abbr),
        "captured_at": date.today().isoformat(),
        "active": True,
        "disqualified": [],
    }


def load_registry(registry_path: Optional[Path] = None) -> Dict[str, Any]:
    path = Path(registry_path) if registry_path else DEFAULT_COACH_DONOR_DIR / "registry.json"
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_registry(registry: Dict[str, Any], registry_path: Optional[Path] = None) -> Path:
    path = Path(registry_path) if registry_path else DEFAULT_COACH_DONOR_DIR / "registry.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2, ensure_ascii=False)
    return path


def register_plate(registry: Dict[str, Any], team: str, plate: Dict[str, Any]) -> Dict[str, Any]:
    """Insert/replace a plate in a team-keyed registry (the player registry shape)."""
    abbr = normalize_team(team)
    entry = registry.setdefault(abbr, {
        "name": TEAM_NAMES.get(abbr, abbr),
        "abbr": abbr,
        "plates": [],
    })
    entry.setdefault("plates", [])
    entry["plates"] = [p for p in entry["plates"] if p.get("id") != plate.get("id")]
    entry["plates"].append(plate)
    entry["plates"].sort(key=lambda p: str(p.get("id", "")))
    registry.setdefault("version", 1)
    registry["generated_at"] = date.today().isoformat()
    return registry


# ----------------------------------------------------------------------------
# Network
# ----------------------------------------------------------------------------

def _fetch(url: str, timeout: float = 20.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_photo_urls(domain: str) -> Tuple[List[str], Optional[str]]:
    """Try both coach-page forms and return the raw photo URLs and page used."""
    for path in ("/team/coaches/", "/team/coaches-roster/"):
        page = f"https://www.{domain}{path}"
        try:
            html = _fetch(page, timeout=25.0).decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 - one page failing is expected
            logger.debug("Could not fetch %s: %s", page, exc)
            continue
        urls = extract_photo_urls(html)
        if urls:
            return urls, page
    return [], None


def build_library(
    target_teams: Optional[Iterable[str]] = None,
    download_images: bool = True,
    out_dir: Optional[Path] = None,
    plates_per_team: int = 3,
) -> Dict[str, Any]:
    """Collect and register 2-3 coach plates per team."""
    out_dir = Path(out_dir) if out_dir else DEFAULT_COACH_DONOR_DIR
    registry = load_registry(out_dir / "registry.json")
    teams = [normalize_team(t) for t in target_teams] if target_teams else list(TEAM_CLUB_DOMAINS)

    for abbr in teams:
        domain = TEAM_CLUB_DOMAINS.get(abbr)
        if not domain:
            logger.warning("No club domain known for %s; skipping.", abbr)
            continue
        urls, page = fetch_photo_urls(domain)
        if not urls:
            logger.warning("No coach photos found on %s's coach pages.", abbr)
            continue
        logger.info("%s: %d raw coach photos on %s", abbr, len(urls), page)

        existing = {p.get("id") for p in registry.get(abbr, {}).get("plates", [])}
        index = 1
        for url in urls:
            if index > plates_per_team:
                break
            plate_id = f"{abbr}_{index}"
            if plate_id in existing:
                index += 1
                continue
            if not download_images:
                index += 1
                continue
            try:
                image = Image.open(io.BytesIO(_fetch(url)))
                plate = save_plate(
                    out_dir, abbr, index, image,
                    collar_kind=COLLAR_KINDS[(index - 1) % len(COLLAR_KINDS)],
                    source_page=page,
                    source_url=url,
                )
                register_plate(registry, abbr, plate)
                logger.info("Saved %s from %s", plate_id, url)
            except Exception as exc:  # noqa: BLE001 - one bad photo should not stop the run
                logger.error("Could not save %s from %s: %s", plate_id, url, exc)
            index += 1

    save_registry(registry, out_dir / "registry.json")
    logger.info("Coach donor registry saved to %s", out_dir / "registry.json")
    return registry


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the local coach donor-plate library.")
    parser.add_argument("--teams", help="Comma-separated team abbreviations (e.g. CAR,NO).")
    parser.add_argument("--out-dir", help="Output directory (default data/headshots/coach_donors).")
    parser.add_argument("--plates-per-team", type=int, default=3,
                        help="Plates to keep per team (2-3; default 3).")
    parser.add_argument("--no-download", action="store_true",
                        help="Discover pages without downloading images (writes no plates).")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    teams = [t.strip() for t in args.teams.split(",") if t.strip()] if args.teams else None
    build_library(
        target_teams=teams,
        download_images=not args.no_download,
        out_dir=Path(args.out_dir) if args.out_dir else None,
        plates_per_team=max(2, min(3, args.plates_per_team)),
    )


if __name__ == "__main__":
    main()
