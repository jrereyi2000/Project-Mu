"""Apple Vision OCR extraction for Madden Team Overalls and Rankings screenshots.

Reads team overalls captures (team_overalls/01_<team>.png ... 32_<team>.png) and
extracts:
- team (franchise nickname e.g. 'BEARS', 'PANTHERS')
- team_ovr (overall rating, e.g. 93)
- off_ovr (offensive rating, e.g. 96)
- def_ovr (defensive rating, e.g. 91)
- pass_off_rank (Passing rank, e.g. 1)
- rush_off_rank (Rushing rank, e.g. 1)
- pass_rush_rank (Pass Rush rank, e.g. 1)
- coverage_rank (Coverage rank, e.g. 1)
- blocking_rank (Blocking rank, e.g. 1)
- team_speed_rank (Team Speed rank, e.g. 1)
- division (e.g. 'NFC SOUTH')
- status (e.g. 'CHAMPIONSHIP CONTENDER', 'REBUILDING')
- salary_cap (e.g. '$161M')
- dev traits counts: dev_hidden, dev_xfactor, dev_superstar, dev_star, dev_normal

Outputs team_overalls.csv into the captures run folder.
"""

import csv
import glob
import os
import re
from typing import Any, Dict, List, Optional, Tuple

try:
    import Quartz
    import Vision
    from Foundation import NSURL
    from PIL import Image
    HAS_APPLE_VISION = True
except ImportError:
    HAS_APPLE_VISION = False

KNOWN_TEAMS = {
    "49ERS", "BEARS", "BENGALS", "BILLS", "BRONCOS", "BROWNS", "BUCCANEERS", "CARDINALS",
    "CHARGERS", "CHIEFS", "COLTS", "COMMANDERS", "COWBOYS", "DOLPHINS", "EAGLES", "FALCONS",
    "GIANTS", "JAGUARS", "JETS", "LIONS", "PACKERS", "PANTHERS", "PATRIOTS", "RAIDERS",
    "RAMS", "RAVENS", "SAINTS", "SEAHAWKS", "STEELERS", "TEXANS", "TITANS", "VIKINGS"
}

OUT_COLS = [
    "team",
    "team_ovr",
    "off_ovr",
    "def_ovr",
    "pass_off_rank",
    "rush_off_rank",
    "pass_rush_rank",
    "coverage_rank",
    "blocking_rank",
    "team_speed_rank",
    "division",
    "status",
    "salary_cap",
    "dev_hidden",
    "dev_xfactor",
    "dev_superstar",
    "dev_star",
    "dev_normal",
]


def ocr_image(path: str) -> Tuple[List[Tuple[str, Tuple[float, float, float, float]]], int, int]:
    """Run Apple Vision OCR on an image and return text items with pixel bounding boxes (x0, y0, x1, y1)."""
    if not HAS_APPLE_VISION:
        raise RuntimeError("Apple Vision is not available on this platform")

    url = NSURL.fileURLWithPath_(path)
    img = Image.open(path)
    W, H = img.size
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, None)
    handler.performRequests_error_([req], None)

    items = []
    for obs in req.results():
        b = obs.boundingBox()
        x0 = b.origin.x * W
        y0 = (1.0 - b.origin.y - b.size.height) * H
        x1 = x0 + b.size.width * W
        y1 = y0 + b.size.height * H
        items.append((obs.text().strip(), (x0, y0, x1, y1)))
    return items, W, H


def parse_rank(text: str) -> Optional[int]:
    """Parse rank string like '1st', '4th', '27th' into integer."""
    m = re.search(r"(\d+)", text)
    return int(m.group(1)) if m else None


def extract_team_overalls_frame(path: str, expected_team: Optional[str] = None) -> Dict[str, Any]:
    """Extract overalls, ratings, rankings, franchise info, and dev traits from one team screen."""
    items, W, H = ocr_image(path)

    # 1. Team nickname
    team = None
    # Check header zone (x ~ 1400-2200, y ~ 250-550)
    for t, b in items:
        x0, y0, x1, y1 = b
        if 1400 <= x0 <= 2200 and 250 <= y0 <= 550:
            for tm in KNOWN_TEAMS:
                if tm == t.upper() or tm in t.upper().split():
                    team = tm
                    break
        if team:
            break

    if not team:
        # Fallback to any recognized team in items
        for t, b in items:
            for tm in KNOWN_TEAMS:
                if tm == t.upper() or tm in t.upper().split():
                    team = tm
                    break
            if team:
                break

    if not team and expected_team:
        team = expected_team.upper()

    # 2. Rankings (PASSING, RUSHING, BLOCKING, PASS RUSH, COVERAGE, TEAM SPEED)
    rank_labels = {
        "PASSING": "pass_off_rank",
        "RUSHING": "rush_off_rank",
        "BLOCKING": "blocking_rank",
        "PASS RUSH": "pass_rush_rank",
        "COVERAGE": "coverage_rank",
        "TEAM SPEED": "team_speed_rank",
    }
    ranks: Dict[str, Optional[int]] = {col: None for col in rank_labels.values()}

    label_boxes = {}
    for t, b in items:
        up = t.upper()
        for lab in rank_labels:
            if lab == up or up.startswith(lab):
                label_boxes[lab] = b

    for lab, lbox in label_boxes.items():
        ly_mid = (lbox[1] + lbox[3]) / 2.0
        best_val = None
        min_dy = 999.0
        for t, b in items:
            # Values are in x range 1700-2100 aligned with labels
            if b[0] >= 1700 and b[2] <= 2100:
                y_mid = (b[1] + b[3]) / 2.0
                dy = abs(y_mid - ly_mid)
                if dy < 40 and dy < min_dy:
                    val = parse_rank(t)
                    if val is not None:
                        min_dy = dy
                        best_val = val
        ranks[rank_labels[lab]] = best_val

    # 3. Ratings (OVR, OFF, DEF)
    # Check badge crop: x ~ [0.78 * W, 0.95 * W], y ~ [0.18 * H, 0.28 * H]
    crop_box = (int(W * 0.78), int(H * 0.18), int(W * 0.95), int(H * 0.28))
    img = Image.open(path)
    badge = img.crop(crop_box)
    badge_tmp = f"/tmp/badge_ovr_{os.path.basename(path)}"
    badge.save(badge_tmp)
    try:
        b_items, bW, bH = ocr_image(badge_tmp)
    finally:
        if os.path.exists(badge_tmp):
            os.remove(badge_tmp)

    labels = {}
    for t, b in b_items:
        up = t.upper()
        if "OVR" in up: labels["OVR"] = b
        elif "OFF" in up: labels["OFF"] = b
        elif "DEF" in up: labels["DEF"] = b

    ratings: Dict[str, Optional[int]] = {"OVR": None, "OFF": None, "DEF": None}
    for lab, lbox in labels.items():
        lx_mid = (lbox[0] + lbox[2]) / 2.0
        num_parts = []
        for t, b in b_items:
            m = re.findall(r"\d", t)
            if m and b[3] <= lbox[1] + 12 and b[1] >= lbox[1] - 85:
                x_mid = (b[0] + b[2]) / 2.0
                if abs(x_mid - lx_mid) < 50:
                    num_parts.append((b[0], "".join(m)))
        if num_parts:
            num_parts.sort(key=lambda p: p[0])
            full_num = "".join(p[1] for p in num_parts)
            ratings[lab] = int(full_num) if full_num else None

    # Fallback to full frame items for any missing ratings
    if ratings["OVR"] is None:
        for t, b in items:
            if 2400 <= b[0] <= 2550 and 370 <= b[1] <= 455:
                m = re.search(r"(\d{2})", t)
                if m: ratings["OVR"] = int(m.group(1))
    if ratings["OFF"] is None:
        for t, b in items:
            if 2550 <= b[0] <= 2700 and 370 <= b[1] <= 455:
                m = re.search(r"(\d{2})", t)
                if m: ratings["OFF"] = int(m.group(1))
    if ratings["DEF"] is None:
        for t, b in items:
            if 2700 <= b[0] <= 2850 and 370 <= b[1] <= 455:
                m = re.search(r"(\d{2})", t)
                if m: ratings["DEF"] = int(m.group(1))

    # 4. Franchise Info: Division, Status, Salary Cap
    division = None
    status = None
    salary_cap = None
    for t, b in items:
        up = t.upper()
        if any(d in up for d in ("AFC EAST", "AFC NORTH", "AFC SOUTH", "AFC WEST",
                                "NFC EAST", "NFC NORTH", "NFC SOUTH", "NFC WEST")):
            division = t.strip()
        elif any(s in up for s in ("CHAMPIONSHIP CONTENDER", "PLAYOFF CONTENDER", "PLAYOFF HOPEFUL",
                                  "REBUILDING", "SUPER BOWL FAVORITE")):
            status = t.strip()
        elif re.search(r"^\$[\d.]+[MBK]?$", up):
            salary_cap = t.strip()

    # 5. Dev Traits counts: HIDDEN, X-FACTOR, SUPERSTAR, STAR, NORMAL
    dev_labels = {
        "HIDDEN": "dev_hidden",
        "X-FACTOR": "dev_xfactor",
        "SUPERSTAR": "dev_superstar",
        "STAR": "dev_star",
        "NORMAL": "dev_normal",
    }
    dev_counts: Dict[str, Optional[int]] = {col: None for col in dev_labels.values()}
    dev_boxes = {}
    for t, b in items:
        up = t.upper()
        for lab in dev_labels:
            if lab == up or up.startswith(lab):
                dev_boxes[lab] = b

    for lab, lbox in dev_boxes.items():
        lx_mid = (lbox[0] + lbox[2]) / 2.0
        # Number is directly below label (y_label + ~20..70)
        best_count = None
        min_dx = 999.0
        for t, b in items:
            if b[1] >= lbox[3] - 10 and b[1] <= lbox[3] + 80:
                x_mid = (b[0] + b[2]) / 2.0
                dx = abs(x_mid - lx_mid)
                if dx < 40 and dx < min_dx:
                    m = re.search(r"(\d+)", t)
                    if m:
                        min_dx = dx
                        best_count = int(m.group(1))
        dev_counts[dev_labels[lab]] = best_count

    return {
        "team": team,
        "team_ovr": ratings["OVR"],
        "off_ovr": ratings["OFF"],
        "def_ovr": ratings["DEF"],
        "pass_off_rank": ranks["pass_off_rank"],
        "rush_off_rank": ranks["rush_off_rank"],
        "pass_rush_rank": ranks["pass_rush_rank"],
        "coverage_rank": ranks["coverage_rank"],
        "blocking_rank": ranks["blocking_rank"],
        "team_speed_rank": ranks["team_speed_rank"],
        "division": division,
        "status": status,
        "salary_cap": salary_cap,
        **dev_counts,
    }


def extract_team_overalls_ocr(root: str) -> List[Dict[str, Any]]:
    """Extract all team overalls screenshots in root/team_overalls into root/team_overalls.csv."""
    folder = os.path.join(root, "team_overalls")
    if not os.path.isdir(folder):
        raise FileNotFoundError(f"No team_overalls folder in {root}")

    pngs = sorted(glob.glob(os.path.join(folder, "*.png")))
    if not pngs:
        # Check for webp if already shrunk
        pngs = sorted(glob.glob(os.path.join(folder, "*.webp")))
    if not pngs:
        raise FileNotFoundError(f"No screenshots found in {folder}")

    rows = []
    for path in pngs:
        base = os.path.basename(path)
        # Expected team name from filename e.g. 01_bears.png -> BEARS
        m = re.search(r"^\d+_(.+?)\.(?:png|webp)$", base)
        exp_team = m.group(1).upper() if m else None
        data = extract_team_overalls_frame(path, expected_team=exp_team)
        rows.append(data)

    csv_path = os.path.join(root, "team_overalls.csv")
    with open(csv_path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=OUT_COLS)
        writer.writeheader()
        for r in rows:
            writer.writerow({col: r.get(col) for col in OUT_COLS})

    print(f"Extracted {len(rows)} team overalls -> {csv_path}")
    return rows
