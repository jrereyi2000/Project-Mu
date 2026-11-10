"""Coach headshot generation for Project Mu (COACH_SAVE_WRITES_PLAN §2.7).

A coach Mu invents gets a generated face painted into a real NFL coaching-top
photo, the same two-stage compositing the player pipeline uses
(``headshots/local/jersey_plate.py``), generalised to a polo / quarter-zip /
jacket plate. Starting real coaches keep their real photos and are never
generated.

Outputs per coach (keyed by ``coach_key`` = ``coach-<id>`` or ``coord-<id>``):
* ``static/coaches/{coach_key}.webp``  - 700x508 transparent cutout, as for players;
* ``data/headshots/dds/coaches/{coach_key}.dds`` - 512x512 BC3 portrait texture.

CLI:
    python3 -m headshots.coach_headshots [--season N] [--coach-key K] [--dry-run]
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from PIL import Image

from headshots.headshot_appearance_generator import appearance, parse_skin_tone_group
from headshots.local.donors import normalize_team
from headshots.local.generator import generate_headshot
from headshots.local.jersey_plate import composite_headshot_to_plate, extract_clean_plate
from headshots.portrait_converter.converter import to_portrait

ROOT = Path(__file__).resolve().parent.parent
STATIC_COACHES_DIR = ROOT / "static" / "coaches"
DDS_COACHES_DIR = ROOT / "data" / "headshots" / "dds" / "coaches"
DEFAULT_COACH_DONOR_DIR = ROOT / "data" / "headshots" / "coach_donors"
DSN_DEFAULT = "host=localhost dbname=madden user=postgres password=postgres"

# Positive prompt: coaching clothes only. The word "jersey" must never appear
# here (test_coach_headshots pins it); the negative prompt carries it instead.
COACH_IMAGE_PROMPT = """Create a photorealistic professional sports-league media-day headshot of a
fictional NFL {ROLE} for the {TEAM}. The subject is {FACE}. He is {AGE_CUE},
with {SKIN_TONE} skin, {HAIR}, and {FACIAL_HAIR}.{GLASSES} Build: {BUILD}.
Natural skin texture with visible pores and realistic imperfections, not
airbrushed and not beautified. Relaxed, confident expression with a slight
closed-mouth smile, looking straight into the camera, head level, shoulders
squared with a very slight turn.

He wears a team-colour coaching polo or quarter-zip in {PRIMARY}
({PRIMARY_HEX}) with {SECONDARY} ({SECONDARY_HEX}) trim: a soft collar with a
short button placket or quarter zip, realistic fabric texture and natural
folds. No headset, no hat, no chains, no pads.

Framing: head-and-shoulders crop cut off just below the collar, head filling
about 55-60% of the frame height, centred, with a small amount of headroom,
landscape orientation. The background is a perfectly seamless pure white
(#FFFFFF) studio backdrop with no gradient, vignette, floor, or wall visible.

Lighting: soft two-light studio setup with a large softbox key light from
camera-left at 45 degrees, a fill light from camera-right at about half power,
and a subtle rim light for hair separation from the white background. Gentle
shadow under the chin, no harsh highlights.

Photographic style: shot on a full-frame camera with an 85mm lens at f/5.6,
sharp focus on the eyes, neutral colour grading, accurate skin tones, commercial
sports-media quality.

Do not include any text, watermark, caption, name overlay, extra logos,
scenery, hands, or full body. It must look like a real photograph, not an
illustration or 3D render."""

# The coaching-clothes negative prompt (the plan's exact contract).
COACH_NEGATIVE_PROMPT = (
    "jersey, shoulder pads, helmet, number, football, ball, full body, lower body, "
    "legs, feet, cleats, pants, hands, gloves, pads, gear, headset, collage, split "
    "screen, triptych, turnaround, character sheet, multiple views, multiple people, "
    "multiple angles, stadium, blurry, distorted face, bad anatomy, deformed eyes, "
    "text, watermark"
)

_ROLE_LABELS = {
    "HEADCOACH": "head coach",
    "HEAD_COACH": "head coach",
    "HC": "head coach",
    "OFFENSIVECOORDINATOR": "offensive coordinator",
    "OFFENSIVE_COORDINATOR": "offensive coordinator",
    "OC": "offensive coordinator",
    "DEFENSIVECOORDINATOR": "defensive coordinator",
    "DEFENSIVE_COORDINATOR": "defensive coordinator",
    "DC": "defensive coordinator",
}


# ----------------------------------------------------------------------------
# Profile helpers
# ----------------------------------------------------------------------------

def coach_key_for(profile: Dict[str, Any]) -> str:
    """``coach-<id>`` / ``coord-<id>`` from the profile (or an explicit key)."""
    key = profile.get("coach_key") or profile.get("key")
    if key:
        return str(key)
    person_kind = str(profile.get("person_kind") or "COACH").strip().upper()
    prefix = "coord" if person_kind in ("COORD", "COORDINATOR") else "coach"
    person_id = profile.get("person_id") or profile.get("id")
    if person_id is not None:
        return f"{prefix}-{person_id}"
    name = profile_name(profile) or "unknown"
    return f"{prefix}-{name.strip().lower().replace(' ', '-')}"


def profile_name(profile: Dict[str, Any]) -> str:
    full = str(profile.get("name") or profile.get("full_name") or "").strip()
    if full:
        return full
    first = str(profile.get("first_name") or "").strip()
    last = str(profile.get("last_name") or "").strip()
    return f"{first} {last}".strip()


def profile_role(profile: Dict[str, Any]) -> str:
    raw = str(profile.get("position") or profile.get("role") or "HeadCoach")
    return _ROLE_LABELS.get(raw.strip().upper().replace(" ", ""), "head coach")


def profile_skin_tone_group(profile: Dict[str, Any]) -> Optional[int]:
    """Madden's 1-7 head skin-tone digit, read from an explicit field or the face preset."""
    for key in ("skin_tone_group", "face_shape", "face_preset", "generic_head"):
        group = parse_skin_tone_group(profile.get(key))
        if group is not None:
            return group
    return None


def coach_seed(profile: Dict[str, Any], seed: Optional[int] = None) -> int:
    if seed is not None:
        return int(seed)
    raw = profile.get("seed")
    if isinstance(raw, int):
        return raw
    basis = str(raw or coach_key_for(profile) or profile_name(profile))
    return int(hashlib.sha256(basis.encode("utf-8")).hexdigest(), 16) % (2 ** 31)


def team_palette(team: str) -> Dict[str, Dict[str, str]]:
    """The team's primary/secondary colours from the shared player palette."""
    try:
        from headshots.make_headshot import _color_cache, team_colors

        if team in _color_cache:
            return _color_cache[team]
        # Try the full club name for an abbreviation/city input.
        return team_colors(team)
    except Exception:
        return {
            "primary": {"name": "team colour", "hex": "#333333"},
            "secondary": {"name": "white", "hex": "#FFFFFF"},
        }


def build_coach_prompt(profile: Dict[str, Any], team: str,
                       meta: Optional[Dict[str, Any]] = None) -> str:
    """Fill the coach prompt from the appearance draw. Never contains "jersey"."""
    name = profile_name(profile) or "Coach"
    if meta is None:
        height = int(profile.get("height") or 72)
        weight = float(profile.get("weight") or 210)
        age = int(profile.get("age") or 45)
        _line, meta = appearance(
            name, team, profile_role(profile), height, weight, age,
            role="coach",
            skin_tone_group=profile_skin_tone_group(profile),
            body_type=profile.get("body_type"),
        )
    colors = team_palette(team)
    values = {
        "ROLE": profile_role(profile),
        "TEAM": team,
        "FACE": meta.get("look", "an ordinary-looking man"),
        "AGE_CUE": meta.get("age_cue", "in his 40s"),
        "SKIN_TONE": meta.get("tone", "medium tan"),
        "HAIR": meta.get("hair", "short hair"),
        "FACIAL_HAIR": meta.get("facial", "clean-shaven"),
        "GLASSES": " He wears thin-framed glasses." if meta.get("glasses") else "",
        "BUILD": meta.get("build", "an average athletic build"),
        "PRIMARY": colors["primary"]["name"],
        "PRIMARY_HEX": colors["primary"]["hex"],
        "SECONDARY": colors["secondary"]["name"],
        "SECONDARY_HEX": colors["secondary"]["hex"],
    }
    prompt = COACH_IMAGE_PROMPT
    for key, value in values.items():
        prompt = prompt.replace("{" + key + "}", str(value))
    return prompt


# ----------------------------------------------------------------------------
# Plate selection
# ----------------------------------------------------------------------------

def _resolve_plate_path(value: str, donor_dir: Optional[Path]) -> str:
    path = Path(value)
    if path.is_absolute():
        return str(path)
    candidate = ROOT / path
    if candidate.exists():
        return str(candidate)
    base = Path(donor_dir) if donor_dir else DEFAULT_COACH_DONOR_DIR
    candidate = base / path.name
    return str(candidate)


def select_coach_plate(team: str, profile: Optional[Dict[str, Any]] = None,
                       donor_dir: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Pick an active plate for the team, preferring a matching build."""
    abbr = normalize_team(team)
    base = Path(donor_dir) if donor_dir else DEFAULT_COACH_DONOR_DIR
    registry_path = base / "registry.json"
    if not registry_path.exists():
        return None
    try:
        with open(registry_path, "r", encoding="utf-8") as f:
            registry = json.load(f)
    except (OSError, ValueError):
        return None

    entry = registry.get(abbr) or registry.get(str(team).strip().upper()) or {}
    plates = [p for p in entry.get("plates", []) if p.get("active", True) and p.get("path")]
    if not plates:
        return None

    body_type = str((profile or {}).get("body_type") or "").strip().lower()
    if body_type:
        matched = [p for p in plates if str(p.get("build", "")).lower() == body_type]
        if matched:
            plates = matched

    plate = dict(plates[coach_seed(profile or {}) % len(plates)])
    plate["path"] = _resolve_plate_path(str(plate["path"]), base)
    if plate.get("mask_path"):
        plate["mask_path"] = _resolve_plate_path(str(plate["mask_path"]), base)
    return plate


# ----------------------------------------------------------------------------
# Generation
# ----------------------------------------------------------------------------

def _remove_background(image: Image.Image, session: Any = None) -> Image.Image:
    """Segment the face/plate from its studio background (rembg silueta)."""
    import rembg

    if session is None:
        try:
            from headshots.rookie_headshots import get_thread_rembg_session
            session = get_thread_rembg_session()
        except Exception:
            session = None
    return rembg.remove(image, session=session)


def _fit_site_cutout(image: Image.Image, width: int = 700, height: int = 508) -> Image.Image:
    im = image.convert("RGBA")
    im.thumbnail((width, height), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    canvas.paste(im, ((width - im.width) // 2, (height - im.height) // 2), im)
    return canvas


def _write_dds(webp_path: str, dds_path: str) -> None:
    with open(webp_path, "rb") as f:
        data = f.read()
    dds_bytes = to_portrait(data, width=512, height=512, fmt="DDS_BC3")
    os.makedirs(os.path.dirname(os.path.abspath(dds_path)), exist_ok=True)
    with open(dds_path, "wb") as f:
        f.write(dds_bytes)


def generate_coach_headshot(
    profile: Dict[str, Any],
    team: str,
    *,
    static_dir: Optional[str] = None,
    dds_dir: Optional[str] = None,
    donor_dir: Optional[str] = None,
    backend: Optional[str] = None,
    seed: Optional[int] = None,
    skip_existing: bool = True,
) -> Tuple[str, str]:
    """Generate one coach's WebP and DDS.

    Routes the face through ``headshots.local`` (MockBackend under test) and
    composites it into the team's coaching-top plate with the generalised
    ``jersey_plate`` calls. Returns ``(webp_path, dds_path)``.
    """
    coach_key = coach_key_for(profile)
    webp_path = os.path.join(static_dir or str(STATIC_COACHES_DIR), f"{coach_key}.webp")
    dds_path = os.path.join(dds_dir or str(DDS_COACHES_DIR), f"{coach_key}.dds")
    if skip_existing and os.path.exists(webp_path):
        return webp_path, dds_path

    name = profile_name(profile) or "Coach"
    height = int(profile.get("height") or 72)
    weight = float(profile.get("weight") or 210)
    age = int(profile.get("age") or 45)

    _line, meta = appearance(
        name, team, profile_role(profile), height, weight, age,
        role="coach",
        skin_tone_group=profile_skin_tone_group(profile),
        body_type=profile.get("body_type"),
    )
    prompt = build_coach_prompt(profile, team, meta=meta)

    cfg: Dict[str, Any] = {"negative_prompt": COACH_NEGATIVE_PROMPT}
    plate = None
    try:
        plate = select_coach_plate(team, profile, donor_dir=Path(donor_dir) if donor_dir else None)
    except Exception:
        plate = None
    if plate:
        cfg["donor_plate"] = plate
        cfg["base_image_path"] = plate["path"]
        cfg["mask_image_path"] = plate["mask_path"]

    png_bytes = generate_headshot(
        prompt=prompt,
        seed=coach_seed(profile, seed),
        backend=backend,
        config=cfg,
    )
    face = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    cutout = _remove_background(face)

    if plate:
        clean_plate = extract_clean_plate(
            plate["path"], garment="coach",
            fabric_colors=plate.get("fabric_colors"),
        )
        composite = composite_headshot_to_plate(
            cutout, clean_plate, garment="coach", out_format="none",
        )
        final = _remove_background(composite)
    else:
        final = cutout

    site = _fit_site_cutout(final)
    os.makedirs(os.path.dirname(os.path.abspath(webp_path)), exist_ok=True)
    site.save(webp_path, format="WEBP", quality=90, method=4)
    _write_dds(webp_path, dds_path)
    return webp_path, dds_path


# ----------------------------------------------------------------------------
# Batch (CLI)
# ----------------------------------------------------------------------------

def get_current_season(dsn: Optional[str] = None) -> int:
    import psycopg

    with psycopg.connect(dsn or os.environ.get("DATABASE_URL", DSN_DEFAULT)) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_season FROM league_state WHERE id = 1")
            row = cur.fetchone()
            if row and row[0]:
                return int(row[0])
    return 2036


_COACH_QUERY = """
    SELECT DISTINCT ON (cmp.person_kind, cmp.person_id)
           cmp.person_kind, cmp.person_id, cmp.first_name, cmp.last_name,
           cmp.age, cmp.position, cmp.body_type, cmp.generic_head, cmp.face_shape,
           cmp.seed, cmp.headshot_path, cmp.portrait_status,
           t.name AS team_name, t.abbreviation
      FROM coach_madden_profiles cmp
      JOIN coaches c ON cmp.person_kind = 'COACH' AND c.id = cmp.person_id
      JOIN coach_tenures ct ON ct.coach_id = c.id
       AND %(season)s BETWEEN ct.start_season AND COALESCE(ct.end_season, 9999)
      JOIN teams t ON t.id = ct.team_id
     WHERE cmp.source = 'CREATED'
    UNION ALL
    SELECT DISTINCT ON (cmp.person_kind, cmp.person_id)
           cmp.person_kind, cmp.person_id, cmp.first_name, cmp.last_name,
           cmp.age, cmp.position, cmp.body_type, cmp.generic_head, cmp.face_shape,
           cmp.seed, cmp.headshot_path, cmp.portrait_status,
           t.name AS team_name, t.abbreviation
      FROM coach_madden_profiles cmp
      JOIN coordinators c ON cmp.person_kind = 'COORD' AND c.id = cmp.person_id
      JOIN coordinator_tenures ct ON ct.coordinator_id = c.id
       AND %(season)s BETWEEN ct.start_season AND COALESCE(ct.end_season, 9999)
      JOIN teams t ON t.id = ct.team_id
     WHERE cmp.source = 'CREATED'
"""


def get_created_coaches(season: int, coach_key: Optional[str] = None,
                        dsn: Optional[str] = None) -> list:
    """Every CREATED coach/coordinator profile with its team for the season."""
    import psycopg

    with psycopg.connect(dsn or os.environ.get("DATABASE_URL", DSN_DEFAULT)) as conn:
        with conn.cursor() as cur:
            cur.execute(_COACH_QUERY, {"season": season})
            rows = cur.fetchall()

    profiles = []
    for row in rows:
        profile = {
            "person_kind": row[0], "person_id": row[1],
            "first_name": row[2], "last_name": row[3], "age": row[4],
            "position": row[5], "body_type": row[6], "generic_head": row[7],
            "face_shape": row[8], "seed": row[9], "headshot_path": row[10],
            "portrait_status": row[11], "team": row[13] or row[12],
        }
        profile["coach_key"] = coach_key_for(profile)
        profiles.append(profile)

    if coach_key:
        profiles = [p for p in profiles if p["coach_key"] == coach_key]
    return profiles


def coaches_needing_headshots(profiles: list, static_dir: Optional[str] = None) -> list:
    """Profiles without an existing WebP (created coaches only)."""
    base = static_dir or str(STATIC_COACHES_DIR)
    needing = []
    for profile in profiles:
        path = os.path.join(base, f"{coach_key_for(profile)}.webp")
        if not os.path.exists(path):
            needing.append(profile)
    return needing


def update_profile_headshot(coach_key: str, webp_path: str, dsn: Optional[str] = None) -> None:
    """Record the generated headshot on the profile row."""
    import psycopg

    kind, _, ident = coach_key.partition("-")
    person_kind = "COORD" if kind == "coord" else "COACH"
    with psycopg.connect(dsn or os.environ.get("DATABASE_URL", DSN_DEFAULT)) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE coach_madden_profiles
                      SET headshot_path = %s, portrait_status = 'GENERATED'
                    WHERE person_kind = %s AND person_id = %s""",
                (webp_path, person_kind, int(ident)),
            )
        conn.commit()


def generate_missing_coach_headshots(season: int, coach_key: Optional[str] = None,
                                     dry_run: bool = False, dsn: Optional[str] = None,
                                     log_fn=print) -> Dict[str, Any]:
    profiles = get_created_coaches(season, coach_key=coach_key, dsn=dsn)
    needing = coaches_needing_headshots(profiles)
    already = len(profiles) - len(needing)
    log_fn(f"[coach_headshots] Season {season}: {len(profiles)} CREATED coaches "
           f"({already} already have a headshot, {len(needing)} need generation)")
    report: Dict[str, Any] = {
        "season": season, "total": len(profiles), "already_had": already,
        "generated": 0, "failed": 0, "errors": [],
    }
    if dry_run or not needing:
        return report

    for profile in needing:
        key = coach_key_for(profile)
        try:
            webp_path, _dds_path = generate_coach_headshot(profile, profile["team"])
            update_profile_headshot(key, webp_path, dsn=dsn)
            report["generated"] += 1
            log_fn(f"[coach_headshots] Generated {profile_name(profile)} ({key}) -> {os.path.basename(webp_path)}")
        except Exception as exc:  # noqa: BLE001 - one failure should not stop the batch
            report["failed"] += 1
            report["errors"].append(f"{key}: {exc}")
            log_fn(f"[coach_headshots] ERROR {key}: {exc}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate headshots for Mu's invented coaches.")
    parser.add_argument("--season", type=int, default=None,
                        help="Target season (default: current_season from league_state)")
    parser.add_argument("--coach-key", help="Only this coach key (e.g. coach-311).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Count the coaches needing a headshot without generating.")
    args = parser.parse_args()

    season = args.season if args.season is not None else get_current_season()
    report = generate_missing_coach_headshots(
        season, coach_key=args.coach_key, dry_run=args.dry_run, log_fn=print)
    if report.get("failed", 0) > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
