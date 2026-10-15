"""Apple Vision OCR extraction for Madden Weekly Injury Report screenshots.

Reads team-by-team injury captures (injuries/01_<team>.png ... 32_<team>.png) and
extracts table rows using local Apple Vision OCR:
- TEAM (franchise nickname)
- PLAYER_NAME (list name, e.g. 'C.MILLER', 'J.TYSON')
- POSITION (POS, e.g. 'QB', 'HB', 'WR', 'DT', 'CB')
- OVR (overall rating)
- INJURY_TYPE (diagnosis e.g. 'TURF TOE', 'PCL SPRAIN', 'NONE')
- DURATION (e.g. 'WEEK TO WEEK', '3- 5 WEEKS', '0 WEEKS')
- WEEKS_REMAINING (parsed integer weeks, 0 for healthy/recovered, 1 for week-to-week)
- IS_IR (boolean Injured Reserve status)
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

HOMOGLYPHS = str.maketrans(
    "АВСЕНІЈКМОРЅТХУаеорсухіјΑΒΕΗΙΚΜΝΟΡΤΧΥΖ",
    "ABCEHIJKMOPSTXYaeopcyxijABEHIKMNOPTXYZ"
)

OUT_COLS = [
    "TEAM",
    "PLAYER_NAME",
    "POSITION",
    "OVR",
    "INJURY_TYPE",
    "DURATION",
    "WEEKS_REMAINING",
    "IS_IR"
]

# Standard Madden position codes
KNOWN_POSITIONS = {
    "QB", "HB", "FB", "WR", "TE", "LT", "LG", "C", "RG", "RT",
    "LEDG", "REDG", "LE", "RE", "DT", "SAM", "MIKE", "WILL", "LOLB", "ROLB", "MLB",
    "CB", "FS", "SS", "K", "P", "LS"
}


def ocr_tokens(path: str) -> List[Tuple[str, Tuple[int, int, int, int]]]:
    """Run Apple Vision OCR on an image and return list of (text, (x0, y0, x1, y1))."""
    if not HAS_APPLE_VISION:
        raise RuntimeError("Apple Vision framework is not available (macOS + pyobjc required).")

    img = Image.open(path)
    W, H = img.size
    url = NSURL.fileURLWithPath_(path)
    src = Quartz.CGImageSourceCreateWithURL(url, None)
    if not src:
        return []
    cg = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
    if not cg:
        return []

    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(False)
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, None)
    handler.performRequests_error_([req], None)

    out = []
    for obs in req.results() or []:
        cand = obs.topCandidates_(1)
        if not cand:
            continue
        bb = obs.boundingBox()
        x0 = int(bb.origin.x * W)
        y0 = int((1 - bb.origin.y - bb.size.height) * H)
        x1 = int(x0 + bb.size.width * W)
        y1 = int(y0 + bb.size.height * H)
        t = str(cand[0].string()).translate(HOMOGLYPHS).strip()
        out.append((t, (x0, y0, x1, y1)))
    return out


def parse_weeks_remaining(duration_str: str, injury_type: str = "") -> int:
    """Parse remaining weeks of injury into an integer.
    Examples:
      '0 WEEKS', 'O WEEKS' -> 0
      'WEEK TO WEEK' -> 1
      '1-3 WEEKS' -> 1
      '2 - 4 WEEKS' -> 2
      '4 - 8 WEEKS' -> 4
      'OUT 1 WEEK' -> 1
      'OUT 6 WEEKS' -> 6
      'SEASON' -> 18
    """
    d = duration_str.upper().strip()
    it = injury_type.upper().strip()

    if it in ("NONE", "HEALTHY", "NO INJURY"):
        return 0
    if re.match(r"^[0O]\s*WEEKS?", d):
        return 0
    if "WEEK TO WEEK" in d or "QUESTIONABLE" in d or "PROBABLE" in d:
        return 1
    if "SEASON" in d:
        return 18

    # Check for range: '2 - 4 WEEKS' -> lower bound 2
    m_range = re.search(r"(\d+)\s*[-–]\s*(\d+)\s*WEEKS?", d)
    if m_range:
        return int(m_range.group(1))

    # Check for single number: '3 WEEKS', 'OUT 6 WEEKS'
    m_single = re.search(r"(\d+)\s*WEEKS?", d)
    if m_single:
        return int(m_single.group(1))

    return 1


def parse_team_injury_frame(path: str) -> List[Dict[str, Any]]:
    """Extract all player injury rows from a single team's screenshot."""
    tokens = ocr_tokens(path)
    if not tokens:
        return []

    # Infer team from filename: e.g. '01_raiders.png' -> 'RAIDERS'
    base = os.path.basename(path)
    team_name = "UNKNOWN"
    m_team = re.match(r"^\d+_([a-z0-9]+)\.png$", base)
    if m_team:
        team_name = m_team.group(1).upper()

    # Find table header y position
    header_tokens = [b for t, b in tokens if t.upper() in ("NAME", "INJURY", "IR", "YOVR", "OVR", "POS") and b[1] < 500]
    if not header_tokens:
        header_y = 450
    else:
        header_y = max(b[3] for b in header_tokens)

    # Filter tokens in table region:
    # y between header_y and bottom bar (y ~ 1700), x < 2100 (excluding right-side player card)
    table_tokens = [(t, b) for t, b in tokens if header_y < (b[1] + b[3]) / 2 < 1700 and b[0] < 2100]

    # Check if empty (e.g. NO INJURIES banner or 0 player names)
    if not table_tokens:
        return []
    if any("NO INJUR" in t.upper() for t, _ in table_tokens):
        return []

    # Group tokens by vertical rows (tolerance ~25px center distance)
    # Sort tokens top to bottom
    sorted_tokens = sorted(table_tokens, key=lambda i: (i[1][1] + i[1][3]) / 2)
    rows: List[List[Tuple[str, Tuple[int, int, int, int]]]] = []

    for tok in sorted_tokens:
        cy = (tok[1][1] + tok[1][3]) / 2
        # Find matching row
        matched = False
        for row in rows:
            row_cy = sum((b[1] + b[3]) / 2 for _, b in row) / len(row)
            if abs(cy - row_cy) < 28:
                row.append(tok)
                matched = True
                break
        if not matched:
            rows.append([tok])

    # Extract fields from each row by horizontal bounds
    # NAME:   x < 600
    # POS:    700 <= x < 950
    # OVR:    950 <= x < 1150
    # INJURY: 1150 <= x < 1550
    # LEN:    1550 <= x < 1880
    # IR:     1880 <= x < 2100
    parsed_rows = []

    for row in rows:
        name_parts = []
        pos_parts = []
        ovr_parts = []
        injury_parts = []
        len_parts = []
        ir_parts = []

        # Sort tokens left to right within row
        row.sort(key=lambda i: i[1][0])

        for t, b in row:
            cx = (b[0] + b[2]) / 2
            if cx < 650:
                name_parts.append(t)
            elif 650 <= cx < 950:
                pos_parts.append(t)
            elif 950 <= cx < 1150:
                ovr_parts.append(t)
            elif 1150 <= cx < 1550:
                injury_parts.append(t)
            elif 1550 <= cx < 1880:
                len_parts.append(t)
            elif 1880 <= cx < 2150:
                ir_parts.append(t)

        player_name = " ".join(name_parts).strip()
        # Clean stray suffixes or OCR noise from sort arrows/conv marks
        player_name = re.sub(r"^[•*►\s]+", "", player_name)

        # Ignore rows that have no name or look like headers
        if not player_name or player_name.upper() in ("NAME", "PLAYER NAME", "SORT", "BACK"):
            continue

        raw_pos = "".join(pos_parts).upper().strip()
        # Clean POS homoglyphs / fixes
        pos = raw_pos.replace("0", "O").replace("@", "Q")
        if pos not in KNOWN_POSITIONS:
            # Check if any known pos substring
            for kp in KNOWN_POSITIONS:
                if kp in pos:
                    pos = kp
                    break

        raw_ovr = "".join(ovr_parts).strip()
        ovr_digits = re.findall(r"\d+", raw_ovr)
        ovr = int(ovr_digits[0]) if ovr_digits else 0

        injury_type = " ".join(injury_parts).strip().upper()
        if not injury_type:
            injury_type = "UNKNOWN"

        duration = " ".join(len_parts).strip().upper()
        # Homoglyph fix: 'O WEEKS' -> '0 WEEKS'
        duration = re.sub(r"^O\s*WEEK", "0 WEEK", duration)

        ir_raw = "".join(ir_parts).upper().strip()
        is_ir = "YES" in ir_raw

        weeks_remaining = parse_weeks_remaining(duration, injury_type)

        parsed_rows.append({
            "TEAM": team_name,
            "PLAYER_NAME": player_name,
            "POSITION": pos,
            "OVR": ovr,
            "INJURY_TYPE": injury_type,
            "DURATION": duration,
            "WEEKS_REMAINING": weeks_remaining,
            "IS_IR": is_ir
        })

    return parsed_rows


def extract_injuries_ocr(captures_dir: str) -> Optional[str]:
    """Extract injuries from all screenshots in captures_dir/injuries/ and write injuries.csv.

    Returns the path to injuries.csv, or None if no injury frames were found.
    """
    injury_dir = os.path.join(captures_dir, "injuries")
    frames = sorted(glob.glob(os.path.join(injury_dir, "*.png")))
    if not frames:
        return None

    all_rows = []
    print(f"Extracting injuries from {len(frames)} team screenshots via Apple Vision OCR...")

    for f in frames:
        rows = parse_team_injury_frame(f)
        all_rows.extend(rows)
        team = rows[0]["TEAM"] if rows else os.path.basename(f)
        print(f"  {team:<15}: {len(rows)} injured players")

    out_csv = os.path.join(captures_dir, "injuries.csv")
    with open(out_csv, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=OUT_COLS)
        writer.writeheader()
        for r in all_rows:
            writer.writerow(r)

    print(f"Extracted {len(all_rows)} total injury records -> {out_csv}")
    return out_csv


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python3 -m pipeline.extract_injuries_ocr <captures_run_dir>")
        sys.exit(1)
    extract_injuries_ocr(sys.argv[1])
