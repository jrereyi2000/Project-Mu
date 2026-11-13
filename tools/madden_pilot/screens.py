"""Screen frames and classification for the Madden pilot (plan section 2.2).

A Frame is one screenshot plus its Windows OCR words. classify() names the screen from
catalog.json: the first screen (in catalog order) whose `all` words are all present, at least
one `any` word is present (when listed), no `none` word is present and every `line_is` phrase
is one whole OCR line; else "UNKNOWN".

Words are matched upper-case, punctuation-blind, on whole words inside one OCR line, so
"FRANCHISE" doesn't match "CURRENT FRANCHISES" and a phrase never spans two lines. `line_is`
exists for a word that also appears inside a longer ticker line: the receiving kickoff's
standalone KICKOFF banner is not the same evidence as "(11:00 Q1) KICKOFF ON CHI 35".
"""
import fnmatch
import json
import re
from pathlib import Path

CATALOG = Path(__file__).with_name("catalog.json")
UNKNOWN = "UNKNOWN"


def norm(s):
    """Upper-case, keep letters, digits, & and -, collapse everything else to single spaces."""
    return " ".join(re.sub(r"[^A-Z0-9&\-]+", " ", str(s).upper()).split())


def label_match(label, pattern):
    """True when an on-screen label matches a catalog label or fnmatch pattern (TUNE-*).

    Windows OCR sometimes loses the space inside a two-word label ("ADVANCEWEEK",
    "COACHCENTRAL", P2-01 2026-11-10), so a multi-word label also matches its space-stripped form.
    """
    pat = " ".join(re.sub(r"[^A-Z0-9&\-*?]+", " ", str(pattern).upper()).split())
    lab = norm(label)
    if fnmatch.fnmatchcase(lab, pat):
        return True
    if " " in pat and not any(ch in pat for ch in "*?["):
        return lab.replace(" ", "") == pat.replace(" ", "")
    return False


def _union(boxes):
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[0] + b[2] for b in boxes)
    y1 = max(b[1] + b[3] for b in boxes)
    return [x0, y0, x1 - x0, y1 - y0]


class Frame:
    """One look: `png` path, OCR `words` ([{"text", "box": [x, y, w, h], "line"}]) and `lines`.

    `lines` defaults to the words grouped by their `line` index (or one line per word when no
    index is given). It may also be given as a list of strings.
    """

    def __init__(self, png=None, words=None, lines=None, size=None):
        self.png = png
        self.size = size
        self.words = [w if isinstance(w, dict) else {"text": str(w)} for w in (words or [])]
        groups = {}
        for i, w in enumerate(self.words):
            groups.setdefault(w.get("line", f"w{i}"), []).append(w)
        self._groups = list(groups.values())          # words per line, in OCR order
        if lines is None:
            lines = [" ".join(w["text"] for w in g) for g in self._groups]
        self.lines = [ln if isinstance(ln, str) else ln.get("text", "") for ln in lines]
        self._norm_lines = [norm(ln) for ln in self.lines]

    @property
    def text(self):
        """All lines, upper-cased, joined with ' | '."""
        return " | ".join(ln.upper() for ln in self.lines)

    def has(self, phrase):
        """True when `phrase` appears as whole words inside one OCR line.

        A multi-word phrase also matches when OCR dropped the space between its words
        ("ADVANCE WEEK" -> "ADVANCEWEEK"), which P2-01 met on the Madden hub.
        """
        p = norm(phrase)
        if not p:
            return False
        if any(f" {p} " in f" {ln} " for ln in self._norm_lines):
            return True
        if " " in p:
            compact = p.replace(" ", "")
            return any(compact in ln.replace(" ", "") for ln in self._norm_lines)
        return False

    def find(self, phrase):
        """The [x, y, w, h] box of `phrase` (a run of consecutive OCR words on one line, or one
        word containing it). None when it isn't on screen or the words carry no boxes."""
        p = norm(phrase)
        if not p:
            return None
        for g in self._groups:
            toks = [norm(w["text"]) for w in g]
            for i in range(len(toks)):
                joined = ""
                for j in range(i, len(toks)):
                    joined = f"{joined} {toks[j]}".strip()
                    if (joined == p or (j == i and f" {p} " in f" {joined} ")
                            or (" " in p and joined.replace(" ", "") == p.replace(" ", ""))):
                        boxes = [w["box"] for w in g[i:j + 1] if w.get("box")]
                        return _union(boxes) if boxes else None
                    if len(joined) >= len(p):
                        break
        return None

    def lines_with_rows(self):
        """[(line text, centre y of its words or None)] in OCR order, for positional rules.

        `self.lines` is authoritative (a caller can pass lines without words); the row comes
        from the matching word group when there is one.
        """
        out = []
        for index, line in enumerate(self.lines):
            group = self._groups[index] if index < len(self._groups) else []
            centres = [float(w["box"][1]) + float(w["box"][3]) / 2
                       for w in group if w.get("box")]
            out.append((line, min(centres) if centres else None))
        return out

    def line_is(self, phrase):
        """True when one whole OCR line equals `phrase` (punctuation-blind)."""
        p = norm(phrase)
        return bool(p) and any(ln == p for ln in self._norm_lines)

    def near(self, phrase, other, max_px=300):
        """True when both are on screen and their box centres are within `max_px`."""
        a, b = self.find(phrase), self.find(other)
        if not a or not b:
            return False
        ax, ay = a[0] + a[2] / 2, a[1] + a[3] / 2
        bx, by = b[0] + b[2] / 2, b[1] + b[3] / 2
        return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5 <= max_px


def load_catalog(path=None):
    """catalog.json as a dict; screens keep their file order (classification order)."""
    with open(path or CATALOG, encoding="utf-8") as f:
        return json.load(f)


def matches(frame, spec):
    if not all(frame.has(w) for w in spec.get("all", [])):
        return False
    if not all(frame.line_is(w) for w in spec.get("line_is", [])):
        return False
    anys = spec.get("any", [])
    if anys and not any(frame.has(w) for w in anys):
        return False
    return not any(frame.has(w) for w in spec.get("none", []))


def classify(frame, catalog):
    """The first catalog screen the frame matches, else UNKNOWN. A screen with neither
    `all` nor `any` words never matches."""
    for name, spec in catalog["screens"].items():
        if not spec.get("all") and not spec.get("any"):
            continue
        if matches(frame, spec):
            return name
    return UNKNOWN


def _row_run(px, row, x_center, width, bright):
    """The contiguous bright run of `row` that contains (or is nearest to) `x_center`.

    Returns (start, end) as exclusive-end pixel indices, or None when the row has no bright
    pixel within a sane distance of the label. A filled selection is bright across its whole
    row, and an outlined one is bright on its border row; both are solid outside the text.
    """
    if row < 0:
        return None
    start = min(width - 1, max(0, int(x_center)))
    if not (0 <= start < width):
        return None
    if px[start, row] < bright:                      # nudge to the nearest bright pixel
        found = None
        for step in range(1, max(4, int(width * 0.08))):
            for candidate in (start - step, start + step):
                if 0 <= candidate < width and px[candidate, row] >= bright:
                    found = candidate
                    break
            if found is not None:
                break
        if found is None:
            return None
        start = found
    left = start
    while left > 0 and px[left - 1, row] >= bright:
        left -= 1
    right = start
    while right + 1 < width and px[right + 1, row] >= bright:
        right += 1
    return left, right + 1


def _covered_bands(px, start, step, height, x0, x1, bright, cover, width, limit=8):
    """Bands of consecutive rows near `start` that are bright across [x0, x1), outward.

    Used only for a tall selection rectangle whose top border is far from the label. Bounded to
    `limit` bands so an ordinary menu look does not scan the whole frame for every option.
    """
    def covers(row):
        if row < 0 or row >= height or x1 <= x0:
            return False
        lit = sum(1 for column in range(x0, x1) if px[column, row] >= bright)
        return lit / (x1 - x0) >= cover

    bands: list[list[int]] = []
    current: list[int] = []
    row = start
    while 0 <= row < height and len(bands) < limit:
        if covers(row):
            if current and abs(row - current[-1]) <= 4:
                current.append(row)
            else:
                if current:
                    bands.append(current)
                current = [row]
        elif current:
            bands.append(current)
            current = []
            if len(bands) >= limit:
                break
        row += step
    if current and len(bands) < limit:
        bands.append(current)
    return bands


def _band_run(px, band, center, width, bright):
    """The widest bright run around `center` on the band's edges (rounded corners vary)."""
    runs = [_row_run(px, row, center, width, bright) for row in (band[0], band[-1])]
    runs = [run for run in runs if run is not None]
    if not runs:
        return None
    return max(runs, key=lambda run: run[1] - run[0])


def _side_columns(px, rows, edge, width, bright, window):
    """True when a bright column joins `rows` just inside `edge`, i.e. a rectangle's side."""
    if not rows:
        return False
    for x in range(max(0, edge - window), min(width, edge + window + 1)):
        if sum(1 for row in rows if px[x, row] >= bright) / len(rows) >= 0.6:
            return True
    return False


def _enclosing_rectangle(px, up_band, down_band, left, right, bright, width):
    """True when `up_band` and `down_band` are the top and bottom edges of **one** rectangle.

    A closed panel is a rectangle, so a bright vertical border joins its two horizontal edges on
    the left and on the right. Two unrelated horizontal rules that happen to span the label (a
    popup's bottom rule above and a button outline below, the ADVANCE_MENU neighbour bleed of
    N1) have no such sides and are refused here. The side columns sit just inside the horizontal
    runs (rounded corners) and are sampled, so the cost is bounded by the pair's own height
    however tall the card is.
    """
    top, bottom = up_band[-1], down_band[0]
    if bottom - top <= 2:
        return False
    step = max(1, (bottom - top) // 48)
    rows = list(range(top + 1, bottom, step))
    window = max(3, int(width * 0.006))
    return (_side_columns(px, rows, left, width, bright, window)
            and _side_columns(px, rows, right, width, bright, window))


def _encloses(px, up_band, down_band, left, right, bright, width, height):
    """True when the two bands enclose one tall card/panel the label sits inside.

    Either the pair is a **closed shape** (bright sides join it, `_enclosing_rectangle`) or it
    spans at least a third of the frame's height. A card's own border is far outside `reach` by
    definition — that is why this fallback exists — while two rules that merely both span the
    label sit a text row or two apart: on ADVANCE_MENU the popup's bottom rule and the ADVANCE
    WEEK button's outline are 133-263 px apart at 1080p/1321p (0.12-0.20 of the height), and
    the measured cards are 642 px in FRANCHISE_LIST and 677 px in the draft-class panel (0.59
    and 0.63). The draft-class panel's own sides are dark, which is why height is also enough.
    """
    if down_band[0] - up_band[-1] >= height * 0.35:
        return True
    return _enclosing_rectangle(px, up_band, down_band, left, right, bright, width)


def border_highlight(frame, labels, reach=0.06, bright=170, cover=0.85, aliases=None):
    """The one label whose OCR box lies **inside** Madden's selection rectangle, or None.

    Madden marks the selected row either with a white outline around it or a light filled row
    behind it. The old reader took the brightest row within `reach` x height of a label and
    chose the nearest label, which handed the selected row's border to the *next* row when the
    selected row's own OCR text was truncated or missing (SUPERSIM_MENU's selected "JUMP TO
    NEXT PLAY" read as "JUMP TO NEXT"; the modal's selected NO is not read at all — F9/F11).
    Selection is now decided by geometry first:

    * bright rows within `reach` x height of a label are grouped into bands;
    * a band that vertically contains the label's box is a filled selection, and the label is
      inside it only when the band's bright run also spans the label horizontally;
    * two separate bands (one above, one below the label) are an outline, accepted only when
      both runs span the label and their left edges agree (the old "nearest bright row" could
      never tell these apart);
    * a tall card's far-apart top and bottom borders are paired only when both bands are thin
      border lines and the card's own side borders join them (a closed shape), so an unrelated
      rule above and a button outline below cannot be read as a card around a neighbour's row;
    * nothing bright, or a label outside every region, means "not selected" — the caller stops
      before Space rather than transferring a neighbour's border.

    `aliases` maps a catalog label to the other readings a screen has actually shown for the
    same row (an observed truncated label), so the row can still be recognized without letting
    any generic phrase match a different action.
    """
    if not frame.png or not Path(frame.png).exists():
        return None
    from PIL import Image
    with Image.open(frame.png) as opened:
        img = opened.convert("L")
        width, height = img.size
        px = img.load()
        span = max(4, int(height * reach))
        aliases = aliases or {}
        boxes: list[tuple[str, tuple[float, float, float, float]]] = []
        for label in labels:
            box = frame.find(label)
            if box is None:
                for reading in aliases.get(label) or []:
                    box = frame.find(reading)
                    if box is not None:
                        break
            if box:
                x, y, w, h = (float(v) for v in box)
                if w > 0 and h > 0:
                    boxes.append((label, (x, y, w, h)))
        if not boxes:
            return None

        def covers(row, x0, x1):
            if row < 0 or row >= height or x1 <= x0:
                return False
            lit = sum(1 for column in range(x0, x1) if px[column, row] >= bright)
            return lit / (x1 - x0) >= cover

        hits: list[str] = []
        for label, (x, y, w, h) in boxes:
            x0, x1 = max(0, int(x)), min(width, int(x + w))
            if x1 <= x0:
                continue
            center = (x0 + x1) // 2
            rows = list(range(max(0, int(y) - span), max(0, int(y)))) + \
                list(range(min(height, int(y + h)), min(height, int(y + h) + span)))
            covered = [row for row in rows if covers(row, x0, x1)]
            bands: list[list[int]] = []
            for row in covered:
                if bands and row - bands[-1][-1] <= 4:
                    bands[-1].append(row)
                else:
                    bands.append([row])
            runs: list[tuple[int, int, int, int]] = []      # (top, bottom, run_left, run_right)
            for band in bands:
                run = _row_run(px, band[0], center, width, bright) or \
                    _row_run(px, band[-1], center, width, bright)
                if run is None:
                    continue
                if run[0] <= x0 + 2 and run[1] >= x1 - 2:
                    runs.append((band[0], band[-1], run[0], run[1]))

            filled = any(top <= y + 2 and bottom >= y + h - 2 for top, bottom, *_ in runs)
            outlined = False
            above = [band for band in runs if band[1] <= y + 2]
            below = [band for band in runs if band[0] >= y + h - 2]
            # A row's selection rectangle is taller than its text (1080p: ~110 px vs 24 px).
            max_gap = max(6 * h, 0.10 * height)
            for up in above:
                for down in below:
                    if down[0] - up[1] < 0 or down[0] - up[1] > max_gap:
                        continue
                    if abs(up[2] - down[2]) <= max(24.0, 0.15 * w):
                        outlined = True
                        break
                if outlined:
                    break
            if not (filled or outlined):
                # A tall portrait card (FRANCHISE_LIST's tiles are ~642 px at 1080p) has its
                # top border far outside `reach` of the name at the bottom of the card, and
                # the card's own artwork can produce nearer bright rows. Collect covered rows
                # outward on each side in bands (bounded), then pair a thin band above with a
                # thin band below: both runs must span the label, agree on BOTH edges, and be
                # joined by the card's own side borders. The thinness rule keeps a filled
                # selected row (ADVANCE_MENU's ~40-row white fill) from being an edge, and the
                # closed-shape rule keeps two unrelated horizontal rules that both span the
                # label (that fill's row above, the ADVANCE WEEK button's outline below) from
                # reading as a card (N1).
                edge_tol = max(6.0, 0.02 * width)
                thin = max(6, int(height * 0.006))
                above_bands = _covered_bands(px, int(y) - 1, -1, height, x0, x1,
                                             bright, cover, width)
                below_bands = _covered_bands(px, int(y + h), 1, height, x0, x1,
                                             bright, cover, width)
                for up_band in above_bands:
                    if len(up_band) > thin:
                        continue
                    up_run = _band_run(px, up_band, center, width, bright)
                    if not up_run or up_run[0] > x0 or up_run[1] < x1:
                        continue
                    for down_band in below_bands:
                        if len(down_band) > thin:
                            continue
                        down_run = _band_run(px, down_band, center, width, bright)
                        if not down_run or down_run[0] > x0 or down_run[1] < x1:
                            continue
                        if (abs(up_run[0] - down_run[0]) <= edge_tol
                                and abs(up_run[1] - down_run[1]) <= edge_tol
                                and _encloses(px, up_band, down_band,
                                              (up_run[0] + down_run[0]) // 2,
                                              (up_run[1] + down_run[1]) // 2,
                                              bright, width, height)):
                            outlined = True
                            break
                    if outlined:
                        break
            if filled or outlined:
                hits.append(label)

        distinct = list(dict.fromkeys(hits))
        return distinct[0] if len(distinct) == 1 else None
