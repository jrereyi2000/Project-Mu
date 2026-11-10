"""Screen frames and classification for the Madden pilot (plan section 2.2).

A Frame is one screenshot plus its Windows OCR words. classify() names the screen from
catalog.json: the first screen (in catalog order) whose `all` words are all present, at least
one `any` word is present (when listed), and no `none` word is present; else "UNKNOWN".

Words are matched upper-case, punctuation-blind, on whole words inside one OCR line, so
"FRANCHISE" doesn't match "CURRENT FRANCHISES" and a phrase never spans two lines.
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
                    if joined == p or (j == i and f" {p} " in f" {joined} "):
                        boxes = [w["box"] for w in g[i:j + 1] if w.get("box")]
                        return _union(boxes) if boxes else None
                    if len(joined) >= len(p):
                        break
        return None

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


def border_highlight(frame, labels, reach=0.06, bright=170, cover=0.85):
    """Which of `labels` has Madden's white selection border ('border' highlight mode).

    Madden outlines the selected option (franchise tiles, GAMEDAY buttons, dialog buttons)
    with a white line. For each label's OCR box, look at the rows within `reach` x height
    above and below it: the label is highlighted when one of those rows is bright
    (luma >= `bright`) across at least `cover` of the label's width. Returns the label whose
    best row is brightest, or None (no image, no boxes, or nothing bright).
    """
    if not frame.png or not Path(frame.png).exists():
        return None
    from PIL import Image
    img = Image.open(frame.png).convert("L")
    W, H = img.size
    px = img.load()
    span = max(4, int(H * reach))
    best, best_score = None, 0.0
    for lab in labels:
        box = frame.find(lab)
        if not box:
            continue
        x, y, w, h = (int(v) for v in box)
        x0, x1 = max(0, x), min(W, x + w)
        if x1 <= x0:
            continue
        rows = list(range(max(0, y - span), max(0, y))) + list(range(min(H, y + h), min(H, y + h + span)))
        for r in rows:
            lit = sum(1 for c in range(x0, x1) if px[c, r] >= bright)
            score = lit / (x1 - x0)
            if score >= cover and score > best_score:
                best, best_score = lab, score
    return best
