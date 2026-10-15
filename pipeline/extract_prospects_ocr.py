"""Apple Vision OCR extraction for Madden Draft Board prospect screenshots.

Reads row-by-row prospect captures (prospect_000.png ... prospect_031.png) and
extracts the highlighted prospect's measurables and details using local Apple Vision OCR:
- Projected Pick (1-32 deterministic index)
- Full Name (top card first + last name, with diacritic & homoglyph normalization)
- Position (top card badge, e.g. LEDG, SAM, CB, QB)
- College (top card COLLEGE line)
- Height and Weight (top card HT/WT line, read glyph by glyph against digit templates; Vision's
  reading with font fixups as the fallback: 6'T" -> 6'1", 5'1" -> 5'11", 827 -> 327)
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


# ------------------------------------------------------------ HT/WT digits ----
# Vision misreads the card's condensed digits differently from run to run (1<->7, 3<->8, 5'11" as
# 5'1"). The HT/WT value always sits at the same spot in one font, so it is read glyph by glyph
# instead: split the band into characters, match each digit against templates, and settle
# 0/3/6/8/9 by their loops. Templates: assets/prospect_htwt_digits.png, digits 0-9, each 18x34,
# the median of ~3,700 glyphs from the Oct 15 board captures (31 cards checked by eye).
HTWT_BOX = (1075, 352, 1420, 400)     # value after "HT/WT:" on a 3024x1888 frame
GLYPH_THR = 170                       # white text on the dark card
GLYPH_W, GLYPH_H = 18, 34
GLYPH_SHIFT = 2                       # stream frames land a pixel or two apart
DIGIT_TEMPLATES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "prospect_htwt_digits.png")
_DIGIT_TEMPLATES = None
HTWT_RE = re.compile(r"^(\d)'(\d{1,2})'+,?(\d{3})")   # glyph text, e.g. 5'11'',214 (the comma can be covered)


def _digit_templates():
    global _DIGIT_TEMPLATES
    if _DIGIT_TEMPLATES is None:
        import numpy as np
        strip = np.asarray(Image.open(DIGIT_TEMPLATES_PATH).convert("L"), dtype=np.float32) / 255.0
        _DIGIT_TEMPLATES = [strip[:, i * GLYPH_W:(i + 1) * GLYPH_W] for i in range(10)]
    return _DIGIT_TEMPLATES


def _glyph_columns(on):
    """Runs of columns with ink: [(x0, x1, y0, y1)]."""
    import numpy as np
    cols = on.sum(0) >= 2
    out, x = [], 0
    while x < len(cols):
        if cols[x]:
            s = x
            while x < len(cols) and cols[x]:
                x += 1
            ys = np.where(on[:, s:x].any(1))[0]
            out.append((s, x, int(ys.min()), int(ys.max()) + 1))
        else:
            x += 1
    return out


def _glyph_distance(c, t):
    """L1 distance between a glyph and a template, best over small shifts."""
    import numpy as np
    s = GLYPH_SHIFT
    pc = np.pad(c, s)
    return min(float(np.abs(pc[s + dy:s + dy + GLYPH_H, s + dx:s + dx + GLYPH_W] - t).sum())
               for dy in range(-s, s + 1) for dx in range(-s, s + 1))


def _holes(on) -> List[Tuple[float, float]]:
    """Enclosed background regions of a binarized glyph: [(centre y, height)] as fractions of its height."""
    import numpy as np
    h, w = on.shape
    seen = np.zeros_like(on, bool)
    out = []
    for sy in range(h):
        for sx in range(w):
            if on[sy, sx] or seen[sy, sx]:
                continue
            stack, ys, border = [(sy, sx)], [], False
            seen[sy, sx] = True
            while stack:
                y, x = stack.pop()
                ys.append(y)
                border = border or y in (0, h - 1) or x in (0, w - 1)
                for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                    if 0 <= ny < h and 0 <= nx < w and not on[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
            if not border and len(ys) >= 4:
                out.append(((min(ys) + max(ys)) / 2 / h, (max(ys) - min(ys) + 1) / h))
    return out


def _digit_by_loops(on) -> str:
    """0/3/6/8/9 differ mostly by their loops, which pixel distance underweights: 8 has two, 3 none,
    0 one tall one, 6 one low, 9 one high."""
    hs = _holes(on)
    if len(hs) >= 2:
        return "8"
    if not hs:
        return "3"
    cy, ht = hs[0]
    if ht > 0.5:
        return "0"
    return "6" if cy > 0.5 else "9"


def glyph_text(img) -> str:
    """The HT/WT value as glyph text: digits, ' for a mark at the top, , for one at the bottom;
    letters (LBS) read as digits too and are ignored by the caller."""
    import numpy as np
    g = np.asarray(img.convert("L").crop(HTWT_BOX), dtype=np.float32)
    on = g > GLYPH_THR
    templates = _digit_templates()
    out = []
    for x0, x1, y0, y1 in _glyph_columns(on):
        if y1 - y0 < 25:                       # quote marks sit at the top, the comma at the bottom
            out.append("," if y0 > 20 else "'")
            continue
        while x1 - x0 > 1 and not on[y0 + 14:y1, x1 - 1].any():
            x1 -= 1                            # a quote mark glued to the digit's right edge
        c = np.zeros((GLYPH_H, GLYPH_W), np.float32)
        piece = g[y0:y0 + GLYPH_H, x0:x1][:, :GLYPH_W] / 255.0
        c[:piece.shape[0], :piece.shape[1]] = piece
        d = int(np.argmin([_glyph_distance(c, t) for t in templates]))
        digit = str(d)
        if digit in "03689":
            digit = _digit_by_loops(on[y0:y1, x0:x1])
        out.append(digit)
    return "".join(out)


def read_ht_wt(img) -> Tuple[str, str]:
    """(height, weight) read glyph by glyph, or ("", "") when the band isn't the expected
    shape or the values are implausible (the caller then uses Vision's reading)."""
    if img.size != (3024, 1888):               # boxes and templates were mapped at this window size
        return "", ""
    m = HTWT_RE.match(glyph_text(img))
    if not m:
        return "", ""
    feet, inches, weight = m.group(1), int(m.group(2)), int(m.group(3))
    if feet not in "56" or inches > 11 or not 140 <= weight <= 420:
        return "", ""
    return f"{feet}'{inches}\"", str(weight)


FIRST_NAME_BAND = (950, 160, 1700, 245)    # card first name (wide-tracked; CHRISTOPHER reaches x 1650)
LAST_NAME_BAND = (950, 237, 1700, 348)     # card last name (tall; ink spans y 239-342)
NAME_RE = re.compile(r"^[A-Z][A-Z.'\- ]*[A-Z.]$")


def read_name_band(img, box) -> str:
    """One card name line read from its band alone, at 2x (then 1x if that isn't a clean name).
    Returns "" when neither read is a clean upper-case name (the caller keeps the full-frame read)."""
    import tempfile
    band = img.crop(box)
    for scale in (2, 1):
        im = band.resize((band.width * scale, band.height * scale), Image.LANCZOS) if scale != 1 else band
        fd, tmp = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            im.save(tmp)
            toks = ocr_tokens(tmp)
        finally:
            os.remove(tmp)
        lines, garbled = join_name_lines([((y0 + y1) // 2, x0, x1, clean_unicode(t))
                                          for t, (x0, y0, x1, y1) in toks])
        text = ""
        # the band can catch the edge of the line above or below: keep its tallest line
        if lines and not garbled:
            text = max(lines, key=lambda ln: _line_height(toks, ln[0]))[1].upper()
        if NAME_RE.match(text):
            return text
    return ""


def edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _fills_in(short: str, full: str) -> bool:
    """`full` could be `short` with letters restored: all of short's letters in order (JAM -> JAMAL,
    SAIA -> ISAIAH), or the same length one letter off (AMIF -> AMIR). A band read backwards
    (NOD for DON) is neither."""
    if len(short) == len(full):
        return edit_distance(short, full) <= 1
    it = iter(full)
    return all(c in it for c in short)


def _line_height(toks, cy) -> int:
    return max((y1 - y0 for _, (x0, y0, x1, y1) in toks if abs((y0 + y1) // 2 - cy) < 25), default=0)


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
        # one letter misread in the table ("N.Croshy" for CROSBY): initial + last name one edit off
        close = [cy for clean_t, cy in names
                 if clean_fi and clean_t[:1] == clean_fi and len(clean_ln) >= 4
                 and edit_distance(clean_t[1:], clean_ln) <= 1]
        if len(close) == 1:
            return close[0]
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
            # the HT/WT line can come back as Cyrillic look-alikes ("нт/wT: Б1\", 235LBS")
            if not any(up.startswith(k) for k in ["HT/WT", "COLLEGE", "EXP", "ROOKIE"]) \
                    and "/W" not in up and "LBS" not in up:
                card_tokens.append(((y0 + y1) // 2, x0, x1, clean_unicode(t)))
    card_name_parts, garbled = join_name_lines(card_tokens)

    # The wide-tracked first name reads better from its band alone than from the whole frame,
    # which drops or doubles its letters (JAM for JAMAL, SAIA for ISAIAH). It only stands in for a
    # close full-frame read (a few letters off), never a different name. (The last name reads worse
    # from its band: Vision drops the narrow I there, e.g. WHTLOCK.)
    first = read_name_band(img, FIRST_NAME_BAND)
    frame_last = card_name_parts[-1][1].upper() if card_name_parts else ""
    frame_first = card_name_parts[0][1].upper() if len(card_name_parts) > 1 else ""
    # a garbled frame read (overlapping pieces, RC + ONALD) has nothing worth keeping
    if first and first != frame_last and (not frame_first or garbled or _fills_in(frame_first, first)):
        frame_first = first
    card_name_parts = [(180, n) for n in (frame_first, frame_last) if n]

    full_name = " ".join(t for _, t in card_name_parts)
    full_name = re.sub(r"\.{2,}", ".", full_name)                  # P.J.. -> P.J.
    full_name = re.sub(r"^([A-Z]{2,})\. ", r"\1 ", full_name)       # PAT. DENT -> PAT DENT (C.J. stays)
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
        # Vision drops one 1 of 5'11" (every 5'1" checked on Oct 15's boards was 5'11")
        if feet == "5" and inches == "1":
            inches = "11"
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

    # The glyph reader is right where Vision flips digits; Vision stays the fallback
    g_height, g_weight = read_ht_wt(img)
    height, weight = g_height or height, g_weight or weight

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
            # "LEDG #72 | OVR75"; the bar can drop out and the number split ("TE #1 1 OVR61")
            m = re.search(r"\b([A-Z]{1,4})\s*#\s*[\dIL ]+\s*\|?\s*[O0]VR\s*(\d{2})", t.upper())
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
    table_name = ""
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
                if 650 <= x0 <= 900 and not table_name:
                    table_name = t

    # Last name by vote: the full-frame card read stands unless the card's last-name band read
    # and the table's short name ("J.Samuel") agree on something else (SAMUEI -> SAMUEL,
    # RUTSON -> HUTSON). Alone, each misreads its own letters (WHTLOCK, MeMillan).
    if table_name and last_name:
        table_last = re.sub(r"^[^.]*\.\s*", "", table_name).upper()
        band_last = read_name_band(img, LAST_NAME_BAND)
        if last_name.upper().replace(" ", "") == table_last.replace(" ", "") and last_name.upper() != table_last:
            # Vision splits a wide last name at a letter gap ("THO MAS"); the table spaces it right
            full_name = full_name[:len(full_name) - len(last_name)] + table_last
        elif band_last and band_last.replace(" ", "") == table_last.replace(" ", "") \
                and edit_distance(table_last.replace(" ", ""), last_name.upper().replace(" ", "")) in (1, 2):
            # letters by vote, spacing from the table (the band can split a name too)
            full_name = full_name[:len(full_name) - len(last_name)] + table_last

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


def _pick_key(rec: Dict[str, str]) -> Optional[Tuple[int, int]]:
    rnd, pk = rec["PROJECTED_ROUND"], rec["ROUND_PICK"]
    if rnd and rnd != UNDRAFTED and pk:
        return int(rnd), int(pk)
    return None


def _next_picks(key: Tuple[int, int]) -> List[Tuple[int, int]]:
    """The picks that can follow `key` on the board: the next in the round, or the next round's first."""
    return [(key[0], key[1] + 1), (key[0] + 1, 1)]


def fix_pick_sequence(records: List[Dict[str, str]]) -> Tuple[int, List[Tuple[Tuple[int, int], Tuple[int, int]]]]:
    """The capture steps one row per frame, so the drafted picks run consecutively. A PICK cell that
    breaks the run (Vision reads 3<->8, 5<->6) or is unreadable is set to the one pick that fits both
    the row before and the row after. A break no single pick mends is a real gap (a skipped row) and is
    left alone. Returns (cells corrected, gaps as (before, after))."""
    fixed, gaps = 0, []
    prev = (1, 0)                                   # before Round 1, Pick 1
    for i, rec in enumerate(records):
        if rec["PROJECTED_ROUND"] == UNDRAFTED:
            break
        cur = _pick_key(rec)
        if cur in _next_picks(prev):
            prev = cur
            continue
        nxt = next((records[j] for j in range(i + 1, min(i + 2, len(records)))), None)
        nxt_key = _pick_key(nxt) if nxt else None
        if nxt_key is not None:
            fits = [c for c in _next_picks(prev) if nxt_key in _next_picks(c)]
        elif nxt is None or nxt["PROJECTED_ROUND"] == UNDRAFTED:
            fits = [] if cur else [(prev[0], prev[1] + 1)]   # the board's last drafted pick
        else:
            fits = []
        if len(fits) == 1:
            rec["PROJECTED_ROUND"], rec["ROUND_PICK"] = str(fits[0][0]), str(fits[0][1])
            fixed += 1
            prev = fits[0]
        elif cur is not None:
            gaps.append((prev, cur))
            prev = cur
    return fixed, gaps


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
    fixed, gaps = fix_pick_sequence(unique)
    if fixed:
        print(f"  prospects: {fixed} PICK cells set from the rows around them")
    for before, after in gaps:
        print(f"  WARNING: the board jumps from Round {before[0]}, Pick {before[1]} to "
              f"Round {after[0]}, Pick {after[1]}; a row may have been skipped")

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
