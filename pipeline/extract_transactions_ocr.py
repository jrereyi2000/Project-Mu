"""
League Transactions pages -> transactions.csv (docs/team_management/02_LEAGUE_TRANSACTIONS_PLAN.md, phase 3).

transactions/page_NNN.png (newest first, ~4 rows a page, 3-row steps) are read with on-device Apple Vision.
Each row is a heading ("2036 SEASON - WEEK 4") over one line of text; the rows of consecutive pages are lined
up on their overlap (not de-duplicated: identical rows are legitimate). Rows the grammar can't place, whose
heading breaks the newest-first order, whose signing has no length or amount, or whose team isn't one of the 32
are re-read by Gemini, one cropped row per request (plan §2.4). A row that still fails is written with
PARSE_OK=0 (the loader stores it as OTHER). Rows that aren't roster moves (transactions_parse.is_move: notices,
commissioner edits, position changes) are dropped before the pages are lined up; the loader would skip them.

The loader re-parses HEADING/TEXT, so the parsed columns are for review and a parser fix needs no re-extract.
"""
import csv, glob, os, re

from pipeline.transactions_parse import (clean_heading, fix_heading_order, heading_ordinal, heading_week,
                                         heading_year, is_move, needs_reread, parse_row, row_key)

COLUMNS = ["ORDER", "PAGE", "HEADING", "SEASON", "WEEK", "TEXT", "TYPE", "TEAM", "TO_TEAM", "POS", "NAME", "YEARS",
           "AMOUNT", "PARSE_OK", "FLAGS"]
HEADING_RE = re.compile(r"\b[0-9TILO]{4} ?(?:PRE ?|POST ?|OFF ?)?SEASON ?[-–—:]")
UI_TEXT = {"BACK", "SELECT", "R2", "L2", "R1", "L1", "NFL", "ALL", "SORT", "LEAGUE TRANSACTIONS", "TRANSACTIONS",
           "VIEW PLAYER CARD", "FILTER"}
BOTTOM = 0.845           # the list ends ~0.82 of the height; the News Center ticker (~0.88) and the button bar
LEFT = 0.10              # sit below it, faint background text sits left of the list (x < ~0.05; list from ~0.115)
# Down moves a highlight before the list scrolls (JR, 2026-10-19; 4 rows show), so two pages can overlap by
# 1 to 4 rows: pages are lined up on the longest overlap that fits.
EXPECTED_OVERLAP = None


def _band(items, tol):
    """Items in reading order: grouped into lines by y (within tol pixels), each line left to right."""
    lines = []
    for t, b in sorted(items, key=lambda i: (i[1][1], i[1][0])):
        if lines and abs(b[1] - lines[-1][0]) <= tol:
            lines[-1][1].append((t, b))
        else:
            lines.append([b[1], [(t, b)]])
    return [" ".join(t for t, _ in sorted(ln, key=lambda i: i[1][0])) for _, ln in lines]


def assemble_rows(items, size, page=0):
    """One page's Vision items [(text, (x0, y0, x1, y1))] -> rows [{heading, text, page, box}], top to bottom.
    A row is a heading plus the one text line right under it (rows never wrap, JR 2026-10-19). Anything further
    down before the next heading is a half-visible row at the screen's bottom edge (its heading misread, e.g.
    "99- 208 SEASON - W 0K2") and is dropped. Text above the first heading (the chips, the status bar, or a row
    whose heading scrolled off) and a last heading with no body yet are left out: the neighbouring page has them
    whole."""
    W, H = size
    items = [(t.strip(), b) for t, b in items if t.strip() and b[1] < H * BOTTOM and b[0] >= W * LEFT
             and t.strip().upper() not in UI_TEXT]
    heads = sorted(((t, b) for t, b in items if HEADING_RE.search(t.upper())), key=lambda i: i[1][1])
    rows = []
    for i, (h, hb) in enumerate(heads):
        top = hb[3] - 2
        bottom = heads[i + 1][1][1] if i + 1 < len(heads) else H * BOTTOM
        bottom = min(bottom, hb[3] + 1.2 * (hb[3] - hb[1]))        # the body line starts right under the heading
        body = [(t, b) for t, b in items if top <= b[1] < bottom and (t, b) != (h, hb) and not HEADING_RE.search(t.upper())]
        if not body:
            continue
        tol = max(4, (hb[3] - hb[1]) * 0.5)
        box = (min([hb[0]] + [b[0] for _, b in body]), hb[1], max([hb[2]] + [b[2] for _, b in body]),
               max(b[3] for _, b in body))
        rows.append({"heading": clean_heading(h), "text": " ".join(_band(body, tol)), "page": page, "box": box})
    return rows


def _key(r):
    return (r["heading"], row_key(parse_row(r["text"]), r["text"]))


def _same(a, b):
    """The same list row read on two pages: same heading, and the same key or nearly the same text (OCR reads
    'SQUAD' as 'SOUAD', '2-YEAR,' as '2-YEAR.')."""
    from difflib import SequenceMatcher
    if a["heading"] != b["heading"]:
        return False
    if _key(a) == _key(b):
        return True
    ta, tb = re.sub(r"[^A-Z0-9]", "", a["text"].upper()), re.sub(r"[^A-Z0-9]", "", b["text"].upper())
    return SequenceMatcher(None, ta, tb).ratio() >= 0.92


def merge_pages(pages, expected=EXPECTED_OVERLAP):
    """Line pages of rows up on their overlap: the new page's first k rows repeat the merged list's last k.
    `expected`, when set, wins when it fits (a page of identical rows also fits a longer overlap); otherwise
    (and by default, since the highlight makes the overlap vary) the longest that fits. No overlap at all means a dropped press may have skipped rows: the first new row is
    flagged 'gap?'. Returns the merged rows, each with a 'flags' list."""
    out = []
    for page in pages:
        page = [dict(r, flags=list(r.get("flags", []))) for r in page]
        if not out:
            out.extend(page)
            continue
        fits = [k for k in range(min(len(out), len(page)), 0, -1) if _fits(out[-k:], page[:k])]
        k = expected if expected in fits else (fits[0] if fits else 0)
        if not k and page:
            page[0]["flags"].append("gap?")
        for a, b in zip(out[len(out) - k:], page[:k]):   # a row misread on one page: keep the reading that parses
            if not _same(a, b) and parse_row(b["text"])["ok"] and not parse_row(a["text"])["ok"]:
                a["text"] = b["text"]
                a["flags"].append("overlap_reread")
        out.extend(page[k:])
    return out


def _fits(tail, head):
    """An overlap of k rows fits when every row matches, or, for k >= 3, all but one row after a matching first
    row (a row Vision garbled on one of the two pages, seen on the 2026-10-19 capture)."""
    misses = sum(1 for a, b in zip(tail, head) if not _same(a, b))
    return misses == 0 or (len(tail) >= 3 and misses == 1 and _same(tail[0], head[0]))


def gemini_reader(root):
    """Re-read one cropped row with Gemini (extract.py's client, metered into llm_usage). Returns
    reader(row) -> (heading, text)."""
    from PIL import Image
    from pydantic import BaseModel
    import extract

    class Row(BaseModel):
        heading: str
        text: str

    prompt = ("This is one row of Madden NFL's League Transactions list: a heading such as '2036 SEASON - WEEK 4' "
              "and one line such as 'JAGUARS SIGNED FREE AGENT (WR) DEVONTA TAYLOR TO 1 YEAR, $3.93M CONTRACT'. "
              "Copy the heading and the line exactly as shown, upper case, digits and $ amounts exactly.")

    def read(row):
        path = os.path.join(root, "transactions", f"page_{row['page']:03d}.png")
        with Image.open(path) as im:
            W, H = im.size
        x0, y0, x1, y1 = row["box"]
        box = (max(0, x0 - 40) / W, max(0, y0 - 12) / H, min(W, x1 + 40) / W, min(H, y1 + 12) / H)
        res = extract._generate([prompt, extract.prepare_image(path, box)], Row, f"transactions p{row['page']:03d}")
        return res.heading, res.text
    return read


def reread_flagged(rows, reader):
    """Send flagged rows to `reader` and keep its reading when it parses better. Rows are updated in place."""
    for r in rows:
        p = parse_row(r["text"])
        if not needs_reread(p, r["flags"]) or (heading_ordinal(r["heading"]) or (0, 1))[1] == 0:
            continue
        try:
            heading, text = reader(r)
        except Exception as e:
            r["flags"].append(f"reread_failed:{str(e)[:40]}")
            continue
        q = parse_row(text)
        if q["ok"] and (not p["ok"] or len(q["flags"]) <= len(p["flags"])):
            r["text"] = text
            if "heading_order" in r["flags"] and clean_heading(heading) != r["heading"]:
                r["heading"] = clean_heading(heading)
            r["flags"].append("from_gemini")
    return rows


def to_csv_rows(rows):
    out = []
    for i, r in enumerate(rows):
        p = parse_row(r["text"])
        flags = r["flags"] + [f for f in p["flags"] if f not in r["flags"]]
        out.append({"ORDER": i, "PAGE": r["page"], "HEADING": r["heading"], "SEASON": heading_year(r["heading"]) or "",
                    "WEEK": heading_week(r["heading"]) or "", "TEXT": p["text"], "TYPE": p["type"], "TEAM": p["team"] or "",
                    "TO_TEAM": p["to_team"] or "", "POS": p["pos"] or "", "NAME": p["name"] or "",
                    "YEARS": p["years"] or "", "AMOUNT": p["amount_text"] or "", "PARSE_OK": int(p["ok"]),
                    "FLAGS": ",".join(flags)})
    return out


def extract_transactions_ocr(root, reader=None, ocr=None):
    """transactions/page_NNN.png -> transactions.csv. reader: the Gemini re-read (default gemini_reader; tests
    stub it); ocr: path -> [(text, box)] (default Apple Vision). Returns the number of rows."""
    from PIL import Image
    paths = sorted(glob.glob(os.path.join(root, "transactions", "page_*.png")))
    if not paths:
        return 0
    if ocr is None:
        from pipeline.extract_injuries_ocr import ocr_tokens as ocr
    pages = []
    for path in paths:
        n = int(re.search(r"page_(\d+)", path).group(1))
        with Image.open(path) as im:
            size = im.size
        pages.append(assemble_rows(ocr(path), size, n))
    # rows that aren't roster moves are skipped by the loader anyway; dropping them before the pages are lined up
    # keeps a notice one page misread from breaking the overlap (seen 2026-10-19)
    rows = merge_pages([[r for r in pg if is_move(r["text"])] for pg in pages])
    fix_heading_order(rows)
    # preseason rows are skipped by the loader (the Week 1 roster already has them): never worth a re-read
    flagged = [r for r in rows if needs_reread(parse_row(r["text"]), r["flags"])
               and (heading_ordinal(r["heading"]) or (0, 1))[1] != 0]
    if flagged:
        try:
            reread_flagged(rows, reader or gemini_reader(root))
        except Exception as e:                       # no key / no network: keep the Vision readings, flagged
            print(f"  transactions: Gemini re-read skipped ({e})")
    out = to_csv_rows(rows)
    with open(os.path.join(root, "transactions.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(out)
    bad = sum(1 for r in out if not r["PARSE_OK"])
    print(f"transactions.csv: {len(out)} rows from {len(paths)} pages; {bad} unparsed, {len(flagged)} sent for re-read")
    return len(out)
