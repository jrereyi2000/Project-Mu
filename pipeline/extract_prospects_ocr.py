"""Apple Vision OCR extraction for Madden Draft Board prospect screenshots.

Reads row-by-row prospect captures (prospect_000.png ... prospect_031.png) and
extracts the highlighted prospect's measurables and details using local Apple Vision OCR:
- Projected Pick (1-32 deterministic index)
- Full Name (top card first + last name, with diacritic & homoglyph normalization)
- Position (top card badge, e.g. LEDG, SAM, CB, QB)
- College (top card COLLEGE line)
- Height (top card HT/WT line with font fixup: 6'T" -> 6'1", 5'1" -> 6'1" on heavyweights)
- Weight (top card HT/WT line with lineman OCR fixup: 827 -> 327)
- Age (highlighted table row AGE column)
- OVR (top card badge OVR and table OVR column)
"""

import csv
import glob
import os
import re
import unicodedata
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

# Madden's card font: letters Vision reads in place of digits on the HT/WT line
WEIGHT_FIXUPS = str.maketrans("OUDQILTZSB", "0000111258")
FEET_FIXUPS = str.maketrans("ESG", "556")

OUT_COLS = [
    "PROJECTED_PICK",
    "PROJECTED_ROUND",
    "ROUND_PICK",
    "FULL_NAME",
    "POSITION",
    "COLLEGE",
    "HEIGHT",
    "WEIGHT",
    "AGE",
    "OVR",
    "ARCHETYPE",
    "NOTES"
]


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


def clean_unicode(s: str) -> str:
    """Decompose combining accents (e.g. Ư -> U)."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.strip()


UNDRAFTED = "UDFA"


def parse_pick_label(text: str) -> Tuple[str, str]:
    """Read the table's PICK cell: 'Round 2, Pick 4' -> ("2", "4"); 'Undrafted' -> ("UDFA", "").

    Returns ("", "") when the cell is missing or unreadable.
    """
    t = text.upper().replace("L", "1").replace("I", "1").replace("T", "1")
    m = re.search(r"R[O0]UND\s*(\d)\s*[,.]?\s*P1CK\s*(\d{1,2})", t)
    if m:
        return m.group(1), str(int(m.group(2)))
    if "UNDRAF" in text.upper():
        return UNDRAFTED, ""
    return "", ""


def join_name_lines(tokens) -> Tuple[List[Tuple[int, str]], bool]:
    """Card name tokens (centre y, x0, x1, text) -> [(y, line text)] top to bottom, plus whether the
    first line looked garbled.

    The first name is printed with wide letter spacing, so Vision sometimes splits it
    (MAL + ACHI). Pieces on one line are joined left to right: no space when the gap is
    smaller than a letter, a space otherwise. Pieces whose boxes overlap mean Vision read
    the same letters twice (RC + ONALD); that line is reported as garbled. Lines group by box
    centre: the tall last-name box can start barely below the first name's top edge.
    """
    lines: List[List[Tuple[int, int, int, str]]] = []
    for tok in sorted(tokens):
        if lines and abs(tok[0] - lines[-1][0][0]) < 25:
            lines[-1].append(tok)
        else:
            lines.append([tok])
    out, garbled = [], False
    for i, line in enumerate(lines):
        line.sort(key=lambda t: t[1])
        text = line[0][3]
        for prev, cur in zip(line, line[1:]):
            gap = cur[1] - prev[2]
            if gap < 0 and i == 0:
                garbled = True
            text += ("" if gap < 40 else " ") + cur[3]
        out.append((line[0][0], text))
    return out, garbled


def find_highlighted_row(items, first_name: str, last_name: str,
                         badge_pos: str, badge_ovr: str) -> Optional[int]:
    """Centre y of the table row that matches the card player.

    The table shows names as 'D.Vogel', so an exact first-initial + last-name match wins
    (two PERALTAs can be on screen at once); then a last-name match; then the only row
    whose POS and OVR equal the card badge.
    """
    clean_ln = re.sub(r"[^A-Z]", "", last_name.upper())
    clean_fi = re.sub(r"[^A-Z]", "", first_name.upper())[:1]
    names = [(re.sub(r"[^A-Z]", "", t.upper()), (y0 + y1) // 2)
             for t, (x0, y0, x1, y1) in items if 650 <= x0 <= 900 and 750 <= y0 <= 1650]
    if clean_ln:
        for clean_t, cy in names:
            if clean_fi and clean_t == clean_fi + clean_ln:
                return cy
        for clean_t, cy in names:
            if clean_ln in clean_t or clean_t.endswith(clean_ln[-4:]):
                return cy
    if badge_pos and badge_ovr:
        rows = {}
        for t, (x0, y0, x1, y1) in items:
            if 750 <= y0 <= 1650:
                cy = (y0 + y1) // 2
                key = min(rows, key=lambda k: abs(k - cy), default=None)
                if key is None or abs(key - cy) >= 25:
                    rows[cy] = []
                    key = cy
                rows[key].append((x0, t))
        hits = [cy for cy, cells in rows.items()
                if any(950 <= x <= 1060 and t == badge_pos for x, t in cells)
                and any(1350 <= x <= 1440 and t == badge_ovr for x, t in cells)]
        if len(hits) == 1:
            return hits[0]
    return None


def parse_prospect_frame(path: str, pick_index: int) -> Dict[str, str]:
    """Parse a single prospect screenshot into a structured dictionary."""
    items = ocr_tokens(path)
    img = Image.open(path)
    W, H = img.size

    # 1. Player Name from Card (top-left card: 850 <= x <= 1500, 150 <= y < 335)
    card_tokens = []
    for t, (x0, y0, x1, y1) in items:
        if 850 <= x0 <= 1500 and 150 <= y0 < 335:
            up = t.upper()
            if not any(up.startswith(k) for k in ["HT/WT", "COLLEGE", "EXP", "ROOKIE"]):
                card_tokens.append(((y0 + y1) // 2, x0, x1, clean_unicode(t)))
    card_name_parts, garbled = join_name_lines(card_tokens)

    # Only the last name detected, or the wide-tracked first name came back split into
    # overlapping pieces (RC + ONALD for RONALD): re-read the first-name band on its own
    if len(card_name_parts) == 1 or (garbled and len(card_name_parts) == 2):
        c_img = img.crop((950, 160, 1500, 245))
        tmp_path = f"/tmp/fn_crop_{pick_index}.png"
        c_img.save(tmp_path)
        try:
            c_items = ocr_tokens(tmp_path)
            c_lines, c_garbled = join_name_lines(
                [((y0 + y1) // 2, x0, x1, clean_unicode(t)) for t, (x0, y0, x1, y1) in c_items])
            fn = c_lines[0][1] if c_lines and not c_garbled else ""
            if fn and fn != card_name_parts[-1][1]:
                card_name_parts = [(180, fn), card_name_parts[-1]]
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    full_name = " ".join(t for _, t in card_name_parts)
    full_name = full_name.replace("SHAOUILLE", "SHAQUILLE")
    full_name = full_name.replace("BOBB'", "BOBBY")

    # 2. HT/WT line
    ht_wt_items = [t for t, (x0, y0, x1, y1) in items if 850 <= x0 <= 1500 and 335 <= y0 <= 410]
    ht_wt_raw = " ".join(ht_wt_items)

    # Height parsing
    ht_m = re.search(r"([0-9ESG])\s*['’`]\s*([0-9TlI]{1,2})", ht_wt_raw)
    if not ht_m:
        ht_m = re.search(r"HT/WT:\s*([56])([0-9]|1[0-2])\b", ht_wt_raw)
    height = ""
    if ht_m:
        # Feet digit misread as a letter: E'11" / S'11" -> 5'11", G'2" -> 6'2"
        feet = ht_m.group(1).translate(FEET_FIXUPS)
        inch_str = ht_m.group(2).replace("T", "1").replace("I", "1").replace("l", "1")
        inches = inch_str
        # Madden OCR font dropped digit: 5'1" on heavy prospect is 6'1"
        if feet == "5" and inches == "1":
            feet = "6"
        height = f"{feet}'{inches}\""

    # Weight parsing
    # Digits misread as letters before LBS: 3U2 -> 302, 2I5 -> 215
    wt_m = re.search(r"([0-9OUDQILTZSB]{3})\s*LBS", ht_wt_raw.upper())
    weight = ""
    if wt_m and any(c.isdigit() for c in wt_m.group(1)):
        w_val = int(wt_m.group(1).translate(WEIGHT_FIXUPS))
        # Madden OCR misread 3 as 8 on 300+ lb linemen (e.g. 827 -> 327)
        if w_val > 500:
            w_val = w_val - 500
        weight = str(w_val)

    # 3. College
    college = ""
    for t, (x0, y0, x1, y1) in items:
        if 850 <= x0 <= 1500 and 390 <= y0 <= 460 and "COLLEGE:" in t.upper():
            college = clean_unicode(re.sub(r"^COLLEGE:\s*", "", t, flags=re.I))
            break
    if college.upper() == "TEXASA&M":
        college = "TEXAS A&M"

    # 4. Badge Position and OVR (top card badge: 80 <= x <= 450, 480 <= y <= 560)
    badge_pos, badge_ovr = "", ""
    for t, (x0, y0, x1, y1) in items:
        if 80 <= x0 <= 450 and 480 <= y0 <= 560:
            m = re.search(r"\b([A-Z]{1,4})\s*#\d+\s*\|\s*[O0]VR\s*(\d{2})", t.upper())
            if m:
                badge_pos = m.group(1)
                badge_ovr = m.group(2)
                break

    # 5. Highlighted table row (for AGE, the on-screen PICK label, and POS/OVR fallback)
    first_name = card_name_parts[0][1] if len(card_name_parts) > 1 else ""
    last_name = card_name_parts[-1][1] if card_name_parts else ""
    matched_y = find_highlighted_row(items, first_name, last_name, badge_pos, badge_ovr)

    age = ""
    table_pos = ""
    table_ovr = ""
    pick_label = ""
    if matched_y is not None:
        for t, (x0, y0, x1, y1) in items:
            cy = (y0 + y1) // 2
            if abs(cy - matched_y) < 25:
                if 1150 <= x0 <= 1250 and t.isdigit() and 18 <= int(t) <= 30:
                    age = t
                if 950 <= x0 <= 1060 and not table_pos:
                    table_pos = t
                if 1350 <= x0 <= 1440 and t.isdigit() and 50 <= int(t) <= 99:
                    table_ovr = t
                if x0 < 450 and not pick_label:
                    pick_label = t

    pos = badge_pos or table_pos
    ovr = badge_ovr or table_ovr
    round_, round_pick = parse_pick_label(pick_label)

    return {
        "PROJECTED_PICK": str(pick_index),
        "PROJECTED_ROUND": round_,
        "ROUND_PICK": round_pick,
        "FULL_NAME": full_name,
        "POSITION": pos,
        "COLLEGE": college,
        "HEIGHT": height,
        "WEIGHT": weight,
        "AGE": age,
        "OVR": ovr,
        "ARCHETYPE": "",
        "NOTES": "",
    }


def _same_prospect(a: Dict[str, str], b: Dict[str, str]) -> bool:
    """Two frames show the same player (a resumed capture run repeats its first row)."""
    if a["PROJECTED_ROUND"] and a["PROJECTED_ROUND"] != UNDRAFTED and \
            (a["PROJECTED_ROUND"], a["ROUND_PICK"]) == (b["PROJECTED_ROUND"], b["ROUND_PICK"]):
        return True
    if a["FULL_NAME"] and a["FULL_NAME"] == b["FULL_NAME"] and a["POSITION"] == b["POSITION"]:
        return True
    keys = ("POSITION", "COLLEGE", "HEIGHT", "WEIGHT", "OVR")
    return all(a[k] and a[k] == b[k] for k in keys)


def order_by_screen_pick(records: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Order prospects by the on-screen projected pick and number them overall.

    `records` arrive in capture order. Repeated frames (the first row of a resumed run)
    are dropped, keeping whichever copy has more fields. Drafted prospects sort by
    (round, pick); a row whose PICK cell was unreadable stays right after the row it
    followed on screen. PROJECTED_PICK becomes the overall pick (earlier rounds' sizes as
    seen on screen + pick in round). Undrafted prospects follow in board order, numbered on
    from the last drafted pick.
    """
    unique: List[Dict[str, str]] = []
    for rec in records:
        dup = next((u for u in unique[-3:] if _same_prospect(u, rec)), None)
        if dup is None:
            unique.append(rec)
        elif sum(bool(v) for v in rec.values()) > sum(bool(v) for v in dup.values()):
            unique[unique.index(dup)] = rec
    dropped = len(records) - len(unique)

    keyed = []
    prev = (0, 0)
    unread = 0
    for seq, rec in enumerate(unique):
        rnd, pk = rec["PROJECTED_ROUND"], rec["ROUND_PICK"]
        if rnd == UNDRAFTED:
            key = (99, 0)
        elif rnd:
            key = (int(rnd), int(pk))
        else:
            unread += 1
            key = prev
        keyed.append((key, seq, rec))
        prev = key
    keyed.sort(key=lambda k: (k[0], k[1]))

    round_size: Dict[int, int] = {}
    for (rnd, pk), _, _ in keyed:
        if rnd != 99 and pk:
            round_size[rnd] = max(round_size.get(rnd, 0), pk)
    offsets, total = {}, 0
    for rnd in sorted(round_size):
        offsets[rnd] = total
        total += round_size[rnd]

    out: List[Dict[str, str]] = []
    last = 0
    for (rnd, pk), _, rec in keyed:
        if rec["PROJECTED_ROUND"] and rec["PROJECTED_ROUND"] != UNDRAFTED:
            last = offsets[rnd] + pk
        else:
            last += 1
        rec["PROJECTED_PICK"] = str(last)
        out.append(rec)

    out_of_order = sum(1 for i, (_, seq, _) in enumerate(keyed) if seq != i)
    if dropped or unread or out_of_order:
        print(f"  prospects: {dropped} repeated frames dropped, {unread} unreadable PICK cells, "
              f"{out_of_order} rows reordered by on-screen pick")
    picks = [r["PROJECTED_PICK"] for r in out]
    if len(set(picks)) != len(picks):
        print("  WARNING: two prospects share an on-screen pick; check the PICK cells")
    return out


def extract_prospects_from_frames(paths: List[str]) -> List[Dict[str, str]]:
    """Extract prospects from prospect_*.png screenshots in capture order."""
    records = [parse_prospect_frame(path, idx + 1) for idx, path in enumerate(paths)]
    return order_by_screen_pick(records)


def frame_number(path: str) -> int:
    return int(re.search(r"(\d+)\.png$", path).group(1))


def find_prospect_frames(root: str) -> List[str]:
    """Find all active row-by-row prospect frames (prospect_000.png ...) in capture order.

    Resumed captures are kept in the same folder numbered from 1000, 2000, ..., so frames sort
    by number, not by name (as text, prospect_1000 would land between prospect_100 and 101).
    """
    raw_paths = (glob.glob(os.path.join(root, "prospects", "*.png")) or
                 glob.glob(os.path.join(root, "*.png")))
    # Exclude multi-run prefixes like prospect_1_000.png; keep prospect_NNN.png
    paths = [p for p in raw_paths if re.match(r"^prospect_\d{3,4}\.png$", os.path.basename(p))]
    return sorted(paths, key=frame_number)


def extract_prospects_ocr(root: str) -> Optional[str]:
    """Run local Apple Vision OCR over prospect frames in root and write prospects.csv.

    Returns the path to prospects.csv on success, or None if no frames found.
    """
    paths = find_prospect_frames(root)
    if not paths:
        return None

    print(f"Extracting {len(paths)} prospects via Apple Vision OCR...")
    records = extract_prospects_from_frames(paths)

    out_path = os.path.join(root, "prospects.csv")
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUT_COLS)
        writer.writeheader()
        writer.writerows(records)

    print(f"Wrote {len(records)} prospects to {out_path}")
    return out_path
