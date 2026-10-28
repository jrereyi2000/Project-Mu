"""
Reads the PS5 attribute scan (docs/in_progress/PC_MIGRATION_PLAN.md §2.3): `madden_capture.py --only attributes` screenshots of the
wide Manage Roster > Roster list, turned into every player's 54 ratings without OCR or an LLM.

  python3 -m pipeline.attribute_reader RUN              # captures/RUN/attributes/NN_<team>/ -> attributes/attributes.csv
  python3 -m pipeline.attribute_reader RUN --team PANTHERS
  python3 -m pipeline.attribute_reader train            # rebuild pipeline/attribute_digits.json from tests/fixtures/attributes/

The list (mapped 2026-10-28): NAME, CONV, POS, AGE, OVR, DEV, MOR, SAVINGS, PENALTY, then 54 rating columns SPD .. INJ
(COLUMNS). 11 columns and 14 rows show at once. d-pad right/left moves a column cursor whose full column name shows in
the chip at the top right; the view scrolls once the cursor reaches its edge. NAME is not pinned, so rows are matched
across views by their place on the page, and across sweeps by content (align_shift). The highlight scrolls down to
the 8th row and stays there, so the capture takes a sweep every 7 rows: every player is seen twice, once
not highlighted.

Reading: every number is the same condensed font on a fixed grid. A cell is thresholded, split into digits at blank
columns, and each digit is matched to the nearest of ten averaged digit images (pipeline/attribute_digits.json). The
green progression arrow and the highlight box's border are dropped by shape. Whole-frame Apple Vision missed ~20%
of these cells; the templates read every held-out cell (tests/test_ratings_reader.py). The highlighted row is drawn
bold and white, which the templates read less reliably, so a player's values come from a sweep where his row isn't
highlighted whenever there is one.

Check: the 54 ratings must give back the OVR on the same row through engines/ovr_weights (the top archetype grade of
the player's position; exact for ~93% of players on the PC sample, within 1 for nearly all). The list has no
overall Throw Accuracy column; the check uses the mean of the short, medium and deep accuracies (weights <= 0.003).
"""
import argparse
import csv
import glob
import json
import os
import re
import sys

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES_PATH = os.path.join(ROOT, "pipeline", "attribute_digits.json")
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "attributes")

# (header, chip text, Madden 27 Player.<field>Rating). Order is the list's, left to right.
COLUMNS = [
    ("NAME", "PLAYER NAME", None), ("CONV", "CONVERSATION", None), ("POS", "POSITION", None),
    ("AGE", "PLAYER AGE", None), ("OVR", "OVERALL GRADE", None), ("DEV", "DEVELOPMENT TRAIT", None),
    ("MOR", "MORALE", None), ("SAVINGS", None, None), ("PENALTY", "PLAYER RELEASE PENALTY", None),
    ("SPD", "SPEED RATING", "Speed"), ("ACC", "ACCELERATION RATING", "Acceleration"),
    ("AGI", "AGILITY RATING", "Agility"), ("COD", "CHANGE OF DIRECTION", "ChangeOfDirection"),
    ("STR", "STRENGTH RATING", "Strength"), ("AWR", "AWARENESS RATING", "Awareness"),
    ("CAR", "CARRYING", "Carrying"), ("BCV", "BALL CARRIER VISION", "BCVision"),
    ("BTK", "BREAK TACKLE", "BreakTackle"), ("TRK", "TRUCKING", "Trucking"), ("SFA", "STIFFARM", "StiffArm"),
    ("SPM", "SPIN MOVE", "SpinMove"), ("JKM", "JUKE MOVE", "JukeMove"), ("CTH", "CATCHING", "Catching"),
    ("CIT", "CATCH IN TRAFFIC", "CatchInTraffic"), ("SPC", "SPECTACULAR CATCH", "SpectacularCatch"),
    ("SRR", "SHORT ROUTE RUNNING", "ShortRouteRunning"), ("MRR", "MEDIUM ROUTE RUNNING", "MediumRouteRunning"),
    ("DRR", "DEEP ROUTE RUNNING", "DeepRouteRunning"), ("RLS", "RELEASE", "Release"), ("JMP", "JUMPING", "Jumping"),
    ("THP", "THROW POWER RATING", "ThrowPower"), ("SAC", "SHORT THROW ACCURACY", "ThrowAccuracyShort"),
    ("MAC", "MEDIUM THROW ACCURACY", "ThrowAccuracyMid"), ("DAC", "DEEP THROW ACCURACY", "ThrowAccuracyDeep"),
    ("RUN", "THROW ON THE RUN", "ThrowOnTheRun"), ("TUP", "THROW UNDER PRESSURE", "ThrowUnderPressure"),
    ("BSK", "BREAK SACK", "BreakSack"), ("PAC", "PLAY ACTION", "PlayAction"), ("PBK", "PASS BLOCK", "PassBlock"),
    ("PBP", "PASS BLOCK POWER", "PassBlockPower"), ("PBF", "PASS BLOCK FINESSE", "PassBlockFinesse"),
    ("RBK", "RUN BLOCK", "RunBlock"), ("RBP", "RUN BLOCK POWER", "RunBlockPower"),
    ("RBF", "RUN BLOCK FINESSE", "RunBlockFinesse"), ("LBK", "LEAD BLOCK", "LeadBlock"),
    ("IBL", "IMPACT BLOCKING", "ImpactBlocking"), ("PRC", "PLAY RECOGNITION", "PlayRecognition"),
    ("TAK", "TACKLE", "Tackle"), ("POW", "HIT POWER", "HitPower"), ("BSH", "BLOCK SHEDDING", "BlockShedding"),
    ("FMV", "FINESSE MOVES", "FinesseMoves"), ("PMV", "POWER MOVES", "PowerMoves"), ("PUR", "PURSUIT", "Pursuit"),
    ("MCV", "MAN COVERAGE", "ManCoverage"), ("ZCV", "ZONE COVERAGE", "ZoneCoverage"), ("PRS", "PRESS", "Press"),
    ("RET", "KICK RETURN", "KickReturn"), ("KPW", "KICK POWER", "KickPower"), ("KAC", "KICK ACCURACY", "KickAccuracy"),
    ("LS", "LONG SNAP", "LongSnap"), ("STA", "STAMINA", "Stamina"), ("TGH", "TOUGHNESS", "Toughness"),
    ("INJ", "INJURY", "Injury"),
]
HEADERS = [c[0] for c in COLUMNS]
INDEX = {h: i for i, h in enumerate(HEADERS)}
CHIP_INDEX = {re.sub(r"[^A-Z]", "", c[1]): i for i, c in enumerate(COLUMNS) if c[1]}   # OCR splits STIFF ARM, ...
RATINGS = [c for c in COLUMNS if c[2]]                      # the 54 rating columns
FIRST_RATING = INDEX["SPD"]
LAST = len(COLUMNS) - 1
NUMERIC = {"AGE", "OVR"} | {c[0] for c in RATINGS}
HEADER_ALIASES = {"YOVR": "OVR", "VOVR": "OVR", "▼OVR": "OVR", "IS": "LS", "1S": "LS", "L5": "LS"}

# Geometry as fractions of the chiaki-ng window grab (measured at 3024x1888, title bar included).
HEADER_Y = (0.215, 0.245)
CHIP_Y = (0.165, 0.21)
CHIP_X = (0.55, 0.735)
ROWS = 14
ROW0_Y, ROW_DY = 0.2797, 0.04004
CELL_HW, CELL_HH = 0.0231, 0.0159
COL_DX = 0.0625                  # one column, from CONV on
VISIBLE_X = (0.03, 0.705)        # column centres inside this are fully on screen
NAME_X = (0.02, 0.17)
HIGHLIGHT_LEVEL = 225            # 95th-percentile digit brightness: ~247 on the highlighted row, ~200 elsewhere
INK = 120
AMBIGUOUS = 0.85                 # best / second-best template distance above this flags a digit (correct reads: <= 0.74)


def column_of_chip(text):
    """The cursor's column index from the chip text, or None."""
    return CHIP_INDEX.get(re.sub(r"[^A-Z]", "", (text or "").upper()))


def header_hits(items, size):
    """[(column index, x centre px)] for the headers OCR found in the header band."""
    W, H = size
    out = []
    for t, (x0, y0, x1, y1) in items:
        if not (H * HEADER_Y[0] < y0 < H * HEADER_Y[1]) or x1 > W * VISIBLE_X[1] + W * 0.02:
            continue
        h = HEADER_ALIASES.get(t.strip().upper(), t.strip().upper())
        if h in INDEX:
            out.append((INDEX[h], (x0 + x1) / 2))
    return out


def column_xs(hits, size):
    """{column index: x centre px} for every column fully on screen, from a fit x = a + b*index over the headers
    from CONV on (NAME is wider). None when the headers don't agree on one grid."""
    W, _ = size
    pts = [(i, x) for i, x in hits if i >= INDEX["CONV"]]
    if len(pts) < 4:
        return None
    i, x = np.array(pts, float).T
    b, a = np.polyfit(i, x, 1)
    if abs(b - COL_DX * W) > 0.15 * COL_DX * W or np.abs(a + b * i - x).max() > 0.012 * W:
        return None
    xs = {}
    for k in range(INDEX["CONV"], len(COLUMNS)):
        xc = a + b * k
        if W * VISIBLE_X[0] < xc < W * VISIBLE_X[1]:
            xs[k] = xc
    if any(i == 0 for i, _ in hits):
        xs[0] = next(x for i, x in hits if i == 0)
    return xs


def row_ys(size, offset=0.0):
    _, H = size
    return [H * (ROW0_Y + r * ROW_DY) + offset for r in range(ROWS)]


def row_offset(arr, xs, size):
    """How far this view's rows sit from the standard grid, in pixels. At the end of the list Madden lines the last
    row up with the bottom, ~16 px above the grid (mapped Oct 28). Found from the text lines under the number
    columns: the median distance from each line's centre to the nearest grid row, 0 when there is too little text."""
    W, H = size
    cols = [x for k, x in xs.items() if HEADERS[k] in NUMERIC]
    if not cols:
        return 0.0
    x0, x1 = int(min(cols) - CELL_HW * W), int(max(cols) + CELL_HW * W)
    y0, y1 = int(H * (ROW0_Y - ROW_DY)), int(H * (ROW0_Y + ROWS * ROW_DY))
    g = arr[y0:y1, x0:x1]
    g = g.mean(2) if g.ndim == 3 else g
    prof = (g > INK).sum(1) > 0.02 * (x1 - x0)
    lo, hi = 20 * H / 1888, 45 * H / 1888                   # a line of digits is ~31 px tall at 1888
    grid = np.array(row_ys(size))
    gaps, y = [], 0
    while y < len(prof):
        if not prof[y]:
            y += 1
            continue
        s = y
        while y < len(prof) and prof[y]:
            y += 1
        if lo <= y - s <= hi:
            c = y0 + (s + y) / 2
            gaps.append(c - grid[np.argmin(np.abs(grid - c))])
    return float(np.median(gaps)) if len(gaps) >= 5 else 0.0


# ---------------------------------------------------------------- digits

def load_templates(path=TEMPLATES_PATH):
    with open(path) as fh:
        doc = json.load(fh)
    h, w = doc["size"]
    return {d: np.array(v, float).reshape(h, w) for d, v in doc["digits"].items()}, (h, w)


def cell_mask(img, xc, yc, size):
    """Ink mask of one cell: bright, not green (the progression arrow). img is an RGB or L numpy array."""
    W, H = size
    hw, hh = int(round(CELL_HW * W)), int(round(CELL_HH * H))
    y0, y1, x0, x1 = int(yc) - hh, int(yc) + hh, int(xc) - hw, int(xc) + hw
    c = img[max(0, y0):y1, max(0, x0):x1]
    if c.ndim == 3:
        g = c.mean(2)
        return (g > INK) & ~((c[..., 1] - c[..., 0]) > 40), g
    return c > INK, c


def glyphs(mask, size, shape):
    """The digit images in a cell mask, left to right, each resized to the template shape. Drops runs shorter than
    a digit (the arrow, specks) and runs touching both the top and bottom of the cell (the highlight border)."""
    W, H = size
    min_w, min_h = max(2, round(4 * W / 3024)), round(20 * H / 1888)
    max_w = round(18 * W / 3024)                         # one digit is 10-16 px wide at 3024; two touching, 20+
    mask = mask.copy()
    mask[:, mask.sum(0) >= 0.9 * len(mask)] = False          # the highlight box's side, even where it touches a digit
    mask[mask.sum(1) >= 0.6 * mask.shape[1], :] = False      # its top or bottom edge, in the next row's cell
    cols = mask.any(0)
    out, x, n = [], 0, len(cols)
    while x < n:
        if not cols[x]:
            x += 1
            continue
        s = x
        while x < n and cols[x]:
            x += 1
        run = mask[:, s:x]
        rows = np.where(run.any(1))[0]
        if x - s < min_w or rows[-1] - rows[0] < min_h or (rows[0] == 0 and rows[-1] == len(mask) - 1):
            continue
        # Bold (highlighted) digits can touch: a run wider than one digit is cut at its thinnest middle column.
        parts = [run]
        if x - s > max_w:
            prof = run.sum(0)
            lo, hi = int((x - s) * 0.3), int((x - s) * 0.7)
            cut = lo + int(np.argmin(prof[lo:hi]))
            parts = [run[:, :cut], run[:, cut + 1:]]
        for part in parts:
            rows = np.where(part.any(1))[0]
            if len(rows) == 0:
                continue
            g = part[rows[0]:rows[-1] + 1]
            im = Image.fromarray((g * 255).astype(np.uint8)).resize((shape[1], shape[0]), Image.BILINEAR)
            out.append(np.asarray(im) / 255.0)
    return out


def read_number(mask, size, templates, shape):
    """(int or None, worst best/second-best distance ratio over its digits)."""
    gs = glyphs(mask, size, shape)
    if not gs or len(gs) > 2:
        return None, 1.0
    s, worst = "", 0.0
    for g in gs:
        d = sorted(((float(((g - t) ** 2).sum()), k) for k, t in templates.items()))
        s += d[0][1]
        worst = max(worst, d[0][0] / max(d[1][0], 1e-9))
    return int(s), round(worst, 3)


def train_templates(samples, shape=(28, 16)):
    """samples: [(mask, size, '87')]. Averages every cleanly split digit. Returns {digit: array}."""
    acc = {str(d): [] for d in range(10)}
    for mask, size, label in samples:
        gs = glyphs(mask, size, shape)
        if len(gs) == len(label):
            for g, ch in zip(gs, label):
                acc[ch].append(g)
    missing = [d for d, v in acc.items() if not v]
    if missing:
        raise ValueError(f"no examples of digits {missing}")
    return {d: np.mean(v, 0) for d, v in acc.items()}


# ---------------------------------------------------------------- one view

def frame_array(png, meta=None):
    """(numpy image, window size). A capture saves only the list area of the window grab (meta["crop"] inside
    meta["window"]); it is put back at its place on a blank window so the geometry above applies unchanged."""
    im = Image.open(png)
    if meta and meta.get("crop"):
        W, H = meta["window"]
        x0, y0, x1, y1 = meta["crop"]
        canvas = np.zeros((H, W), dtype=int)
        canvas[y0:y0 + im.height, x0:x0 + im.width] = np.asarray(im.convert("L"))
        return canvas, (W, H)
    return np.asarray(im.convert("RGB")).astype(int), im.size


def read_view(png, items, templates, shape, meta=None):
    """One screenshot of the list. Returns {"columns": {index: [value|None]*14}, "names": [...], "positions": [...],
    "highlight": row or None, "flags": [...]}, or None when the headers can't be placed."""
    arr, size = frame_array(png, meta)
    xs = column_xs(header_hits(items, size), size)
    if xs is None:
        return None
    ys = row_ys(size, row_offset(arr, xs, size))
    view = {"columns": {}, "names": None, "positions": None, "highlight": None, "flags": []}
    bright, unsure = [], []
    for k, xc in sorted(xs.items()):
        if HEADERS[k] not in NUMERIC:
            continue
        vals = []
        for r, yc in enumerate(ys):
            mask, g = cell_mask(arr, xc, yc, size)
            v, amb = read_number(mask, size, templates, shape)
            if v is not None and not 0 <= v <= 99:
                v = None
            if v is not None and amb > AMBIGUOUS:
                unsure.append((r, f"{HEADERS[k]} row {r + 1}: ambiguous digit ({amb})"))
            vals.append(v)
            if mask.any():
                bright.append((r, float(np.percentile(g[mask], 95))))
        view["columns"][k] = vals
    per_row = {}
    for r, b in bright:
        per_row.setdefault(r, []).append(b)
    lit = [r for r, bs in per_row.items() if np.median(bs) > HIGHLIGHT_LEVEL]
    view["highlight"] = lit[0] if len(lit) == 1 else None
    # The bold highlighted row is often a close call; its values are only used when no other read exists.
    view["flags"] = [msg for r, msg in unsure if r != view["highlight"]]
    if 0 in xs:
        view["names"] = _row_texts(items, size, ys, (size[0] * NAME_X[0], size[0] * NAME_X[1]))
    if INDEX["POS"] in xs:
        x = xs[INDEX["POS"]]
        view["positions"] = _row_texts(items, size, ys, (x - CELL_HW * size[0] * 2, x + CELL_HW * size[0] * 2))
        names = view["names"] or [None] * ROWS
        for r, yc in enumerate(ys):
            if view["positions"][r] is None and names[r]:
                view["positions"][r] = _cell_text(arr, x, yc, size)
    return view


POSITION_RE = re.compile(r"^(QB|HB|FB|WR|TE|LT|LG|C|RG|RT|LEDG|REDG|DT|SAM|MIKE|WILL|CB|FS|SS|K|P|LS)$")


def _cell_text(arr, xc, yc, size):
    """Apple Vision on one cell, for the short positions (C, RG, RT) whole-frame OCR skips. None off macOS or when
    the text isn't a position."""
    try:
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        from vision_cells import _vision
    except Exception:
        return None
    from PIL import ImageOps
    W, H = size
    hw, hh = int(CELL_HW * W * 1.6), int(CELL_HH * H)
    c = arr[int(yc) - hh:int(yc) + hh, int(xc) - hw:int(xc) + hw]
    g = c.mean(2) if c.ndim == 3 else c
    im = Image.fromarray(np.clip(g, 0, 255).astype(np.uint8))
    big = im.resize((im.width * 3, im.height * 3), Image.LANCZOS)
    # Vision reads one- and two-letter cells unreliably; each variant catches some (C at 1x, RT at 1x, RG at 3x).
    for variant in (ImageOps.expand(ImageOps.invert(im), 40, fill=255), ImageOps.expand(ImageOps.invert(big), 24, fill=255),
                    ImageOps.expand(big, 24, fill=0), ImageOps.expand(im, 40, fill=0)):
        text = re.sub(r"[^A-Z]", "", _vision(variant)[0].upper())
        if POSITION_RE.match(text):
            return text
    return None


def _row_texts(items, size, ys, xr):
    out = [None] * ROWS
    for t, (x0, y0, x1, y1) in items:
        xc, yc = (x0 + x1) / 2, (y0 + y1) / 2
        if not xr[0] < xc < xr[1]:
            continue
        r = int(round((yc - ys[0]) / (ys[1] - ys[0])))
        if 0 <= r < ROWS and abs(yc - ys[r]) < CELL_HH * size[1]:
            out[r] = t.strip()
    return out


def load_items(txt):
    """The OCR items saved next to a screenshot (madden_capture.py writes `text<TAB>x0,y0,x1,y1` lines, in window
    pixels, after its `key: value` header lines)."""
    items = []
    with open(txt) as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 2 and re.fullmatch(r"-?\d+,-?\d+,-?\d+,-?\d+", parts[1]):
                items.append((parts[0], tuple(int(v) for v in parts[1].split(","))))
    return items


def load_meta(txt):
    """The header lines of a saved view: window W H, crop x0 y0 x1 y1, cursor column, screen."""
    meta = {}
    with open(txt) as fh:
        for line in fh:
            if "\t" in line or ":" not in line:
                continue
            k, v = line.split(":", 1)
            nums = [int(x) for x in v.split() if re.fullmatch(r"-?\d+", x)]
            if k in ("window", "crop") and nums:
                meta[k] = nums
            elif k == "cursor" and nums:
                meta[k] = nums[0]
            else:
                meta[k] = v.strip()
    return meta


# ---------------------------------------------------------------- one team

def sweep_table(views):
    """Merge one sweep's views (same rows, different columns) into a page: {"cells": {index: [..14]},
    "names", "positions", "highlight", "flags"}."""
    page = {"cells": {}, "names": None, "positions": None, "highlight": None, "flags": []}
    lits = set()
    for v in views:
        for k, vals in v["columns"].items():
            old = page["cells"].get(k)
            if old is None:
                page["cells"][k] = list(vals)
            else:
                for r, (a, b) in enumerate(zip(old, vals)):
                    if a is None:
                        old[r] = b
                    elif b is not None and a != b:
                        page["flags"].append(f"{HEADERS[k]} row {r + 1}: views disagree ({a} vs {b})")
        page["names"] = page["names"] or v["names"]
        page["positions"] = page["positions"] or v["positions"]
        if v["highlight"] is not None:
            lits.add(v["highlight"])
        page["flags"] += v["flags"]
    page["highlight"] = lits.pop() if len(lits) == 1 else None
    if len(lits) > 1:
        page["flags"].append("highlight moved within a sweep")
    return page


def _row_key(page, r):
    """Everything known about one row: the numbers by column, then name and position (the left end of the list
    has only AGE and OVR as numbers)."""
    key = [(k, page["cells"][k][r]) for k in sorted(page["cells"])]
    for name in ("names", "positions"):
        key.append((name, (page[name] or [None] * ROWS)[r]))
    return key


def align_shift(prev, page, max_shift=ROWS - 1):
    """How many rows the list moved between two pages: the shift j at which prev's rows j.. match page's rows
    0.. best (rating values, names where both have them). Returns (j, matched rows, compared rows)."""
    best = (0, -1, 0)
    for j in range(0, max_shift + 1):
        match = comp = 0
        for r in range(ROWS - j):
            a, b = dict(_row_key(prev, r + j)), dict(_row_key(page, r))
            pairs = [(a[k], b[k]) for k in a if k in b and a[k] is not None and b[k] is not None]
            if len(pairs) < 3:
                continue
            comp += 1
            if sum(x == y for x, y in pairs) >= 0.8 * len(pairs):
                match += 1
        if comp and (match > best[1] or (match == best[1] and match / comp > best[1] / max(best[2], 1))):
            best = (j, match, comp)
    return best


def team_players(pages):
    """Pages of one team in order -> [player dict] with values taken from a non-highlighted row when possible."""
    tops = [0]
    flags = []
    for p, (a, b) in enumerate(zip(pages, pages[1:]), 1):
        j, match, comp = align_shift(a, b)
        if comp and match < comp:
            flags.append(f"sweep {p + 1}: {comp - match} of {comp} overlapping rows don't match")
        tops.append(tops[-1] + j)
    n = tops[-1] + ROWS
    seen = [dict(reads=[]) for _ in range(n)]
    for page, top in zip(pages, tops):
        for r in range(ROWS):
            seen[top + r]["reads"].append((page, r, r == page["highlight"]))
    players = []
    for i, s in enumerate(seen):
        reads = sorted(s["reads"], key=lambda t: t[2])          # non-highlighted first
        if not reads:
            continue
        p = {"row": i + 1, "flags": [], "values": {}}
        for page, r, lit in reads:
            p.setdefault("name", (page["names"] or [None] * ROWS)[r])
            if not p.get("name"):
                p["name"] = (page["names"] or [None] * ROWS)[r]
            if not p.get("pos"):
                p["pos"] = (page["positions"] or [None] * ROWS)[r]
        clean = [t for t in reads if not t[2]]
        for k in sorted({k for page, _, _ in reads for k in page["cells"]}):
            vals = [page["cells"][k][r] for page, r, _ in clean if k in page["cells"] and page["cells"][k][r] is not None]
            lit_vals = [page["cells"][k][r] for page, r, lit in reads if lit and k in page["cells"]
                        and page["cells"][k][r] is not None]
            if vals:
                if len(set(vals)) > 1:
                    p["flags"].append(f"{HEADERS[k]} read {sorted(set(vals))}")
                elif len(vals) > 1 and k >= FIRST_RATING:
                    p["confirmed"] = p.get("confirmed", 0) + 1
                p["values"][k] = vals[0]
            elif lit_vals:
                p["values"][k] = lit_vals[0]
                p["flags"].append(f"{HEADERS[k]} only seen highlighted")
        if not p.get("name") and not p["values"]:
            continue
        players.append(p)
    # Rows past the roster's end show nothing: drop trailing players without ratings.
    while players and not any(k >= FIRST_RATING for k in players[-1]["values"]):
        players.pop()
    return players, flags


# ---------------------------------------------------------------- OVR check

def ratings_dict(values):
    """{save field: value} for the rating columns read, plus ThrowAccuracy (not on the list)."""
    out = {c[2]: values[INDEX[c[0]]] for c in RATINGS if INDEX[c[0]] in values}
    acc = [out[f] for f in ("ThrowAccuracyShort", "ThrowAccuracyMid", "ThrowAccuracyDeep") if f in out]
    if acc:
        out["ThrowAccuracy"] = int(round(sum(acc) / len(acc)))
    return out


def madden_position(pos):
    from pipeline.load_save import POSITIONS
    back = {mu: madden for madden, mu in POSITIONS.items()}
    p = (pos or "").upper().strip()
    return {"LEDGE": "LE", "REDGE": "RE"}.get(p, back.get(p, p))


def check_ovr(doc, pos, values):
    """(predicted OVR or None, 'ok' | 'off by N' | 'n/a')."""
    from engines import ovr_weights as ow
    mp = madden_position(pos)
    shown = values.get(INDEX["OVR"])
    if mp not in doc["slots"] or shown is None or len([k for k in values if k >= FIRST_RATING]) < len(RATINGS):
        return None, "n/a"
    pred, _ = ow.predict_ovr(doc, mp, ratings_dict(values))
    d = 0 if shown == 99 and pred >= 99 else abs(pred - shown)      # the list shows at most 99
    return pred, "ok" if d <= 1 else f"off by {d}"


# ---------------------------------------------------------------- a run

def read_team(team_dir, templates, shape):
    """captures/RUN/attributes/NN_<team>/ (sNN_vNN.png + .txt) -> (players, flags)."""
    sweeps = {}
    for png in sorted(glob.glob(os.path.join(team_dir, "s*_v*.png"))):
        m = re.match(r"s(\d+)_v(\d+)\.png$", os.path.basename(png))
        if m:
            sweeps.setdefault(int(m.group(1)), []).append(png)
    pages, flags = [], []
    for s in sorted(sweeps):
        views = []
        for png in sweeps[s]:
            txt = png[:-4] + ".txt"
            v = read_view(png, load_items(txt), templates, shape, load_meta(txt))
            if v is None:
                flags.append(f"{os.path.basename(png)}: headers not placed, view skipped")
            else:
                views.append(v)
        if views:
            pages.append(sweep_table(views))
    players, more = team_players(pages)
    return players, flags + more


def read_run(run, team=None, out=None):
    from engines import ovr_weights as ow
    templates, shape = load_templates()
    doc = ow.load_weights()
    rdir = os.path.join(ROOT, "captures", run, "attributes") if not os.path.isdir(run) else os.path.join(run, "attributes")
    dirs = sorted(d for d in glob.glob(os.path.join(rdir, "[0-9]*_*")) if os.path.isdir(d))
    if team:
        dirs = [d for d in dirs if os.path.basename(d).split("_", 1)[1].upper() == team.upper()]
    out = out or os.path.join(rdir, "attributes.csv")
    fields = [c[2] for c in RATINGS]
    totals = {"players": 0, "ok": 0, "off": 0, "n/a": 0}
    with open(out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["team", "row", "name", "pos", "age", "ovr", "predicted_ovr", "ovr_check", "confirmed", "flags"]
                   + fields)
        for d in dirs:
            tname = os.path.basename(d).split("_", 1)[1].upper()
            players, flags = read_team(d, templates, shape)
            for f in flags:
                print(f"  {tname}: {f}")
            for p in players:
                v = p["values"]
                pred, chk = check_ovr(doc, p.get("pos"), v)
                totals["players"] += 1
                totals["ok" if chk == "ok" else "n/a" if chk == "n/a" else "off"] += 1
                w.writerow([tname, p["row"], p.get("name"), p.get("pos"), v.get(INDEX["AGE"]), v.get(INDEX["OVR"]),
                            pred, chk, p.get("confirmed", 0), "; ".join(p["flags"])]
                           + [v.get(INDEX[c[0]]) for c in RATINGS])
            print(f"{tname}: {len(players)} players")
    print(f"Wrote {out}: {totals['players']} players, OVR check ok {totals['ok']}, off {totals['off']}, "
          f"n/a {totals['n/a']}")
    return totals


# ---------------------------------------------------------------- templates

def fixture_samples():
    """(training samples, held-out samples) from tests/fixtures/attributes/labels.json. Each entry: a list-area crop
    (grayscale PNG), the size of the window grab it came from, its column x and row y centres in crop pixels, and
    per row the values, with "train" / "test" marking which columns feed which set."""
    with open(os.path.join(FIXTURES, "labels.json")) as fh:
        doc = json.load(fh)
    train, test = [], []
    for e in doc["views"]:
        arr = np.asarray(Image.open(os.path.join(FIXTURES, e["file"])).convert("L")).astype(int)
        size = tuple(e["window"])
        for col, xc in zip(e["columns"], e["xs"]):
            for r, yc in enumerate(e["ys"]):
                label = e["values"][col][r]
                if label is None:
                    continue
                mask, _ = cell_mask(arr, xc, yc, size)
                lit = r == e.get("highlight")
                (train if col in e["train"] and not lit else test).append((mask, size, str(label), lit, col, r))
    return train, test


def build_templates(path=TEMPLATES_PATH, shape=(28, 16)):
    train, _ = fixture_samples()
    t = train_templates([(m, s, l) for m, s, l, *_ in train], shape)
    with open(path, "w") as fh:
        json.dump({"size": list(shape), "ink": INK, "source": "tests/fixtures/attributes (2026-10-28 Panthers views)",
                   "digits": {d: [round(float(x), 3) for x in a.ravel()] for d, a in sorted(t.items())}}, fh)
    print(f"Wrote {path} from {len(train)} cells")


def main():
    ap = argparse.ArgumentParser(description="Read the PS5 attribute scan into attributes.csv.")
    ap.add_argument("run", help="capture run (captures/RUN or a path), or 'train' to rebuild the digit templates")
    ap.add_argument("--team", help="only this team (nickname, e.g. PANTHERS)")
    ap.add_argument("--out", help="CSV path (default: <run>/attributes/attributes.csv)")
    a = ap.parse_args()
    if a.run == "train":
        build_templates()
        return
    read_run(a.run, a.team, a.out)


if __name__ == "__main__":
    sys.path.insert(0, ROOT)
    main()
