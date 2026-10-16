#!/usr/bin/env python3
"""
Turn madden_capture.py screenshots into CSVs with Gemini vision.

  python3 extract.py captures/2026-10-13_1100

Uses GEMINI_API_KEY (or GOOGLE_API_KEY) from the environment, falling back to the
project-root .env, the same way the newsroom does. Model: gemini-3.8-flash, the one
pipeline/ocr_extractor.py uses (override with MADDEN_EXTRACT_MODEL). Screenshots are cropped to
the table, halved in resolution and read in parallel (MADDEN_EXTRACT_WORKERS, default 8).

Writes, inside that capture folder:
  stats_<category>.csv   one per stats category (rows de-duplicated across pages;
                         Passing/Defense/Kicking: each left page paired with its right page, rows joined by position)
  team_<tab>.csv         team_conversions, team_redzoneefficiency, team_penalties (32 teams each)
  schedule.csv           the captured week's games and results
  schedule_next.csv      postseason: the coming round's matchups and kickoff times (same columns)
  season_schedule.csv    --only season-schedule: every week 1-18 (same columns as schedule.csv)
  games/<NN>_.../box_score.csv, stats_<category>.csv   --only games, per game
  game_scores.csv, game_stats_<category>.csv           --only games, the whole week (GAME column)
  stats_scope_<team>_<category>.csv   --only stats --scope TEAM (Season Stats for one team)
  roster_list.csv        --only roster list pages: TEAM + NAME, POS, AGE, OVR, MOR, ... (icons blank)
  roster_cards.csv       roster/cards.csv (Apple Vision) with AGE/HT/WT/EXP re-read by Gemini from
                         the card crops, 20 per request; vision_* keep Vision's values and
                         vision_disagrees names the fields where the two differ
  injuries.csv           the captured week's injury report across all 32 teams (Apple Vision OCR)
"""
import csv, glob, io, os, sys, threading, time
from collections import Counter, OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed

from google import genai
from google.genai import types
from PIL import Image
from pydantic import BaseModel

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
MODEL = os.environ.get("MADDEN_EXTRACT_MODEL", "gemini-3.8-flash")
WORKERS = int(os.environ.get("MADDEN_EXTRACT_WORKERS", "8"))   # parallel Gemini requests


def _api_key():
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if key:
        return key
    env_path = os.path.join(PROJECT_ROOT, ".env")
    if os.path.exists(env_path):
        with open(env_path) as fh:
            for line in fh:
                line = line.strip()
                if line.startswith(("GEMINI_API_KEY=", "GOOGLE_API_KEY=")):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("Set GEMINI_API_KEY (environment or the project-root .env).")


class Row(BaseModel):
    cells: list[str]


class Table(BaseModel):
    title: str          # the table's category or week label, e.g. PASSING or SEASON WEEK 17
    columns: list[str]
    rows: list[Row]


PROMPT = """This is a screenshot of a Madden NFL franchise table.
Return the table: its title (the category or week label shown on the chip above it, e.g.
PASSING or SEASON WEEK 17), the column headers left to right, and every visible row.
Copy every row exactly as shown, including the highlighted row and any row cut off at the
bottom only if all its cells are readable. Keep numbers as displayed (commas, %, decimals,
minus signs). Use "" for an empty cell. Each row must have one cell per column.
Ignore the player/team banner at the top and the button hints at the bottom."""

ROSTER_PAGE_ROWS = 14
ROSTER_NOTE = """
This is a team roster (the team is on the left chip, e.g. PANTHERS). The CONV and DEV columns
show icons, not text: return "" for them. The small green arrow next to some OVR values is an
icon too: return only the number."""

# Card value strips (AGE, HT, WT, EXP from the roster player card), read in batches.
CARD_STRIP = (0, 295, 560, 400)       # pixels in madden_capture.py's card crop (727x420)
CARD_BATCH = 20


class CardValues(BaseModel):
    index: int
    age: str
    height: str
    weight: str
    experience: str


class CardBatch(BaseModel):
    cards: list[CardValues]


CARD_PROMPT = """Each numbered strip is from a Madden NFL player card and shows AGE, HT (height),
WT (weight) and EXP (years of experience) with their values below the labels. For every strip
return its number (the white box on the left) and the four values exactly as shown, e.g.
age "31", height "6'1\"", weight "257", experience "ROOKIE" or "9". Use "" for a value that is
not visible. Return one entry per strip, in order."""

_client = None
_client_lock = threading.Lock()
_cache = {}          # path -> parsed table, filled by prefetch()


METER = None          # db.llm_usage.UsageMeter on the client: every Gemini call's tokens (llm_usage)


def _get_client():
    global _client, METER
    with _client_lock:
        if _client is None:
            from db.llm_usage import UsageMeter
            _client = genai.Client(api_key=_api_key())
            METER = UsageMeter(_client)
    return _client


def _labelled(label):
    _get_client()
    return METER.labelled(label)


TABLE_BOX = (0, 0.27, 1, 0.89)          # fractions of the screenshot
ROSTER_LIST_BOX = (0, 0.10, 0.735, 0.85)  # roster table only: chips + rows, not the player card


def prepare_image(path, box=TABLE_BOX):
    """Send Gemini only the table: crop off the player/team banner and the button hints, then
    halve the resolution (3024x1888 Retina captures stay sharp at half size). ~10x less upload."""
    img = Image.open(path).convert("RGB")
    W, H = img.size
    img = img.crop((int(W * box[0]), int(H * box[1]), int(W * box[2]), int(H * box[3])))
    if img.width > 1600:
        img = img.resize((img.width // 2, img.height // 2), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return types.Part.from_bytes(data=buf.getvalue(), mime_type="image/png")


def _is_roster(path):
    return os.path.basename(os.path.dirname(os.path.dirname(path))) == "roster"


def _generate(contents, schema, label, retries=4):
    client = _get_client()
    for attempt in range(1, retries + 1):
        try:
            with _labelled(label):
                resp = client.models.generate_content(
                    model=MODEL,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=schema,
                        temperature=0.1,
                    ),
                )
            return schema.model_validate_json(resp.text)
        except Exception as e:
            if attempt == retries:
                raise RuntimeError(f"Gemini couldn't read {label}: {e}") from e
            rate_limited = "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e)
            wait = (10 if rate_limited else 2) * attempt
            print(f"  {label}: attempt {attempt} failed ({str(e)[:120]}); retrying in {wait}s")
            time.sleep(wait)


def _read_table_remote(path, retries=4):
    """One screenshot -> {"title", "columns", "rows": [[cell, ...], ...]}."""
    if _is_game(path):
        t = _generate([PROMPT + GAME_NOTE, prepare_image(path, GAME_TABLE_BOX)], Table,
                      os.path.basename(path), retries)
        return {"title": t.title, "columns": t.columns, "rows": [r.cells for r in t.rows]}
    if _is_roster(path):
        # A roster page shows 14 rows; Gemini sometimes returns fewer (Ravens, Oct 13: 31 of
        # 52 over 5 pages), so a short page is read again and the fullest reading kept.
        best = None
        for attempt in range(3):
            t = _generate([PROMPT + ROSTER_NOTE, prepare_image(path, ROSTER_LIST_BOX)], Table,
                          os.path.basename(path), retries)
            if best is None or len(t.rows) > len(best.rows):
                best = t
            if len(best.rows) >= ROSTER_PAGE_ROWS:
                break
        return {"title": best.title, "columns": best.columns, "rows": [r.cells for r in best.rows]}
    client = _get_client()
    part = prepare_image(path)
    for attempt in range(1, retries + 1):
        try:
            with _labelled(os.path.basename(path)):
                resp = client.models.generate_content(
                    model=MODEL,
                    contents=[PROMPT, part],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=Table,
                        temperature=0.1,
                    ),
                )
            t = Table.model_validate_json(resp.text)
            return {"title": t.title, "columns": t.columns, "rows": [r.cells for r in t.rows]}
        except Exception as e:
            if attempt == retries:
                raise RuntimeError(f"Gemini couldn't read {os.path.basename(path)}: {e}") from e
            rate_limited = "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e)
            wait = (10 if rate_limited else 2) * attempt
            print(f"  {os.path.basename(path)}: attempt {attempt} failed ({str(e)[:120]}); retrying in {wait}s")
            time.sleep(wait)


def prefetch(paths):
    """Read every screenshot in parallel up front; merging then runs in page order from the cache."""
    todo = [p for p in paths if p not in _cache]
    print(f"Reading {len(todo)} screenshots with {WORKERS} parallel Gemini requests...")
    start = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(_read_table_remote, p): p for p in todo}
        for i, fut in enumerate(as_completed(futures), 1):
            _cache[futures[fut]] = fut.result()
            if i % 20 == 0 or i == len(todo):
                print(f"  {i}/{len(todo)} read ({time.time() - start:.0f}s)")


def read_table(path):
    if path not in _cache:
        _cache[path] = _read_table_remote(path)
    return _cache[path]


def merge(paths, label, key_cols=None):
    """Rows of overlapping pages, first copy kept. key_cols (column names) de-duplicates on
    those columns only, for tables where a repeated row can be read slightly differently."""
    columns, rows = None, OrderedDict()
    for p in sorted(paths):
        t = read_table(p)
        columns = columns or t["columns"]
        idx = [columns.index(c) for c in key_cols if c in columns] if key_cols else None
        for n, r in enumerate(t["rows"]):
            k = tuple(r[i] if i < len(r) else "" for i in idx) if idx else tuple(r)
            _seen_at(label, tuple(r), len(t["rows"]) - 1 - n)
            if k not in rows:                 # pages overlap; keep first copy
                rows[k] = r
            elif key_cols and list(rows[k]) != list(r):
                # the same row read two ways on overlapping pages: say so, the values differ
                diff = [f"{c} {a!r}/{b!r}" for c, a, b in zip(columns, rows[k], r) if a != b]
                print(f"  CONFLICT {label} {' '.join(k)}: {', '.join(diff)} (kept the first)")
        print(f"  {os.path.basename(p)}: {len(t['rows'])} rows")
    print(f"{label}: {len(rows)} unique rows")
    return columns, [list(r) for r in rows.values()] if key_cols else list(rows)


def _col(c):
    """Column name without sort marks ("▼TAK", "*YARDS")."""
    return c.strip().lstrip("*▼▲Yv ").upper() if c.strip()[:1] in "*▼▲" else c.strip().upper()


def merge_wide(left_paths, right_paths, label):
    """Wide tables: NAME/POS scroll away, so each right-hand page was captured at the same
    scroll position as its left-hand page. Pair pages by number and rows by position. Each
    page is read through its own headers: a column half-visible at a page edge (a game's
    Defense FMB, Oct 14) is listed on some pages and not others, so one header list for all
    pages would shift values. Columns in both halves keep the non-empty value and must agree."""
    rights = {os.path.basename(p).rsplit("_", 1)[1]: p for p in right_paths}
    columns, rows = [], OrderedDict()
    wide_seen = []
    for lp in sorted(left_paths):
        num = os.path.basename(lp).rsplit("_", 1)[1]
        lt = read_table(lp)
        rt = read_table(rights[num]) if num in rights else {"columns": [], "rows": []}
        lcols, rcols = [_col(c) for c in lt["columns"]], [_col(c) for c in rt["columns"]]
        # only the LAST left column can be cut off at the screen edge; it then reads as the
        # start of its name ("FMB", "FM" for FMBREC) and is that right-page column. (Applying this
        # to every column turned Season Stats' FGM into FGM29.)
        if lcols and lcols[-1] not in rcols:
            c = lcols[-1].replace(" ", "")
            lcols[-1] = next((r for r in rcols if r.replace(" ", "").startswith(c)), lcols[-1])
        for c in lcols + rcols:
            if c not in columns:
                columns.append(c)
        if len(lt["rows"]) != len(rt["rows"]):
            print(f"  WARNING {os.path.basename(lp)}: {len(lt['rows'])} left rows vs "
                  f"{len(rt['rows'])} right rows; pairing the first {min(len(lt['rows']), len(rt['rows']))}")
        bad = 0
        n_rows = min(len(lt["rows"]), len(rt["rows"])) or len(lt["rows"])
        from_bottom = {}
        for i, lr in enumerate(lt["rows"]):
            rr = rt["rows"][i] if i < len(rt["rows"]) else []
            from_bottom[i] = n_rows - 1 - i
            row = {c: v for c, v in zip(lcols, lr)}
            for c, v in zip(rcols, rr):
                if row.get(c, "") == "":
                    row[c] = v
                elif v != "" and row[c] != v:
                    bad += 1               # the two screenshots weren't at the same scroll position
            rows.setdefault(tuple(row.get(c, "") for c in columns), row)
            wide_seen.append((row, from_bottom[i]))
        if bad:
            print(f"  WARNING {os.path.basename(lp)}: {bad} cells disagree between the left and right "
                  "halves; check this page pair")
        print(f"  {os.path.basename(lp)} + right: {len(lt['rows'])} rows")
    # rows found before a later page added a column: re-key on the final header, de-duplicate
    # a half-visible header can also come back as a fragment no column starts with ("FR"):
    # such a column is empty on every row, so drop columns no row has a value for
    columns = [c for c in columns if any(row.get(c, "") != "" for row in rows.values())]
    final = OrderedDict()
    for row in rows.values():
        final.setdefault(tuple(row.get(c, "") for c in columns), None)
    for row, fb in wide_seen:
        _seen_at(label, tuple(row.get(c, "") for c in columns), fb)
    print(f"{label}: {len(final)} unique rows")
    return columns, list(final)


def _close(a, b):
    """Names one OCR slip apart: same length and at most one different letter ("I.Salas" /
    "L.Salas", "M.Livingston" / "M.Levingston"), or the same once dots and spaces are dropped."""
    a, b = a.upper(), b.upper()
    if a.replace(".", "").replace(" ", "") == b.replace(".", "").replace(" ", ""):
        return True
    return len(a) == len(b) and sum(x != y for x, y in zip(a, b)) <= 1


def misread_groups(columns, rows, key_cols):
    """Groups of rows that look like one row read two ways on overlapping pages: same key
    (NAME + POS, or TEAM + NAME) differing in at most 2 cells, or every number equal under names
    one slip apart. Returns lists of row indexes."""
    ki = [columns.index(c) for c in key_cols if c in columns]
    ni = columns.index("NAME") if "NAME" in columns else None
    groups, used = [], set()
    for i, a in enumerate(rows):
        if i in used:
            continue
        g = [i]
        for j in range(i + 1, len(rows)):
            if j in used:
                continue
            b = rows[j]
            diff = [c for c in range(len(columns)) if (a[c] if c < len(a) else "") != (b[c] if c < len(b) else "")]
            same_key = all(a[k] == b[k] for k in ki)
            if (same_key and 0 < len(diff) <= 2) or (
                    ni is not None and diff == [ni] and _close(a[ni], b[ni])):
                g.append(j)
        if len(g) > 1:
            groups.append(g)
            used.update(g)
    return groups


# For each (label, row): the most rows it was ever seen above its page's bottom edge. Rows near the
# bottom are drawn faded or caught mid-scroll and get misread the same way on every read (Oct 14:
# "I.Salas" for L.Salas on a last row; C.Matias' GP blurred between 15 and 16 three rows up); an
# overlapping page shows them higher and clear.
FROM_BOTTOM = {}


def _seen_at(label, row, rows_from_bottom):
    k = (label, row)
    FROM_BOTTOM[k] = max(FROM_BOTTOM.get(k, -1), rows_from_bottom)


def vote_misreads(label, merge_fn, paths, key_cols):
    """merge_fn() -> (columns, rows). If the merged rows hold a row read two ways, read the
    pages twice more and keep, for each such group, the version most reads agree on: one row
    (a misread) or two rows (two players who share a name). Cells are voted on."""
    columns, rows = merge_fn()
    groups = misread_groups(columns, rows, key_cols)
    if not groups:
        return columns, rows
    # first: the copy seen highest above a page's bottom edge beats the others
    drop = set()
    for g in groups:
        height = {i: FROM_BOTTOM.get((label, tuple(rows[i])), -1) for i in g}
        best = max(height.values())
        top = [i for i in g if height[i] == best]
        if len(top) == 1:
            print(f"    {rows[g[0]][0]}: kept the copy seen {best} rows above a page's bottom {tuple(rows[top[0]])}")
            drop.update(i for i in g if i != top[0])
    rows = [r for i, r in enumerate(rows) if i not in drop]
    groups = misread_groups(columns, rows, key_cols)
    if not groups:
        return columns, rows
    print(f"  {label}: {len(groups)} row(s) read two ways; reading the pages twice more to vote")
    versions = []
    for _ in range(2):
        for p in paths:
            _cache.pop(p, None)
        versions.append(merge_fn())
    out, drop = list(rows), set()
    for g in groups:
        first = rows[g[0]]
        def belongs(r, cols):
            if len(r) != len(columns) or cols != columns:
                return False
            return misread_groups(columns, [first, r], key_cols) != [] or list(r) == list(first)
        counts = [len(g)] + [sum(1 for r in vrows if belongs(r, vcols)) for vcols, vrows in versions]
        cands = [rows[i] for i in g] + [r for vcols, vrows in versions for r in vrows if belongs(r, vcols)]
        if sorted(counts)[1] == 1:            # most reads see one row: vote it cell by cell
            voted = tuple(Counter(r[c] for r in cands).most_common(1)[0][0] for c in range(len(columns)))
            print(f"    {first[0] if first else ''}: one row, voted -> {voted}")
            out[g[0]] = voted
            drop.update(g[1:])
        else:
            print(f"    {first[0] if first else ''}: {counts} rows in the reads; kept as separate players")
    return columns, [r for i, r in enumerate(out) if i not in drop]


def card_sheet(paths, first_index):
    """Stack card value strips into one image, each labelled with its index on the left."""
    from PIL import ImageDraw, ImageFont
    strips = [Image.open(p).convert("RGB").crop(CARD_STRIP) for p in paths]
    w, h = CARD_STRIP[2] - CARD_STRIP[0], CARD_STRIP[3] - CARD_STRIP[1]
    sheet = Image.new("RGB", (w + 110, h * len(strips)), "black")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=44)
    for i, im in enumerate(strips):
        y = i * h
        draw.rectangle((6, y + 14, 100, y + h - 14), fill="white")
        draw.text((53, y + h / 2), str(first_index + i), fill="black", font=font, anchor="mm")
        sheet.paste(im, (110, y))
    buf = io.BytesIO()
    sheet.save(buf, format="PNG", optimize=True)
    return types.Part.from_bytes(data=buf.getvalue(), mime_type="image/png")


def read_card_values(root, cards):
    """Gemini reads AGE/HT/WT/EXP for every card (Apple Vision drops the digit 1 and misreads
    heights in this font). Returns {row index in cards: CardValues}."""
    jobs = [cards[i:i + CARD_BATCH] for i in range(0, len(cards), CARD_BATCH)]
    print(f"Reading {len(cards)} card value strips in {len(jobs)} Gemini requests...")
    out = {}

    def one(start):
        batch = cards[start:start + CARD_BATCH]
        part = card_sheet([os.path.join(root, c["card_png"]) for c in batch], start)
        res = _generate([CARD_PROMPT, part], CardBatch, f"cards {start}-{start + len(batch) - 1}")
        return start, len(batch), res

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for start, n, res in pool.map(one, range(0, len(cards), CARD_BATCH)):
            got = {v.index: v for v in res.cards if start <= v.index < start + n}
            if len(got) != n:
                print(f"  WARNING cards {start}-{start + n - 1}: Gemini returned {len(got)} of {n}")
            out.update(got)
    return out


class CardName(BaseModel):
    first: str
    last: str
    number: str
    position: str


CARD_NAME_PROMPT = """This is the top of a Madden NFL player card: the first name (small) above the last
name (large), then a line like "#64 DT | Nose Tackle | Elite Player". Return the first name, the
last name (with any suffix such as Jr. or III, exactly as shown), the jersey number without the #,
and the position abbreviation."""
CARD_NAME_BOX = (0, 30, 726, 200)       # pixels in the card crop: names + number line
NAME_FLAGS = ("missing_first", "missing_last", "missing_number", "missing_position")


def fill_card_names(root, cards):
    """Apple Vision sometimes leaves a card's first name, last name, number or position blank (a
    handful in 1,719 cards on Oct 13-14). Those cards' name area goes to Gemini, one request each;
    the row gets name_from_gemini in its flags. Changes cards in place, returns how many."""
    todo = [c for c in cards if any(f in c["flags"].split(";") for f in NAME_FLAGS)]
    if not todo:
        return 0
    print(f"Reading {len(todo)} card names Apple Vision missed...")

    def one(c):
        img = Image.open(os.path.join(root, c["card_png"])).convert("RGB").crop(CARD_NAME_BOX)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        part = types.Part.from_bytes(data=buf.getvalue(), mime_type="image/png")
        return c, _generate([CARD_NAME_PROMPT, part], CardName, c["card_png"])

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for c, n in pool.map(one, todo):
            for key, val in (("first", n.first), ("last", n.last), ("number", n.number), ("position", n.position)):
                if not c[key].strip() and val.strip():
                    c[key] = val.strip().title() if key in ("first", "last") else val.strip().upper()
            c["flags"] = ";".join(filter(None, [c["flags"], "name_from_gemini"]))
    return len(todo)


def _card_value(key, v):
    v = v.strip().replace("’", "'").replace("”", '"').replace("''", '"')
    if key == "experience" and v.upper() == "ROOKIE":
        return "0"
    return v


def extract_roster(root):
    """roster/<NN>_<team>/list_*.png -> roster_list.csv (TEAM + the list columns), and
    roster/cards.csv (Vision) + Gemini card values -> roster_cards.csv."""
    team_dirs = sorted(glob.glob(os.path.join(root, "roster", "[0-9]*_*")))
    if not team_dirs:
        return
    prefetch(sorted(p for d in team_dirs for p in glob.glob(os.path.join(d, "list_*.png"))))
    columns, rows = None, []
    for d in team_dirs:
        team = os.path.basename(d).split("_", 1)[1].upper()
        cols, trows = merge(glob.glob(os.path.join(d, "list_*.png")), team, key_cols=["NAME", "POS"])
        columns = columns or cols
        rows += [[team] + list(r) for r in trows]
    write_csv(os.path.join(root, "roster_list.csv"), ["TEAM"] + list(columns), rows)

    with open(os.path.join(root, "roster", "cards.csv"), newline="") as fh:
        cards = list(csv.DictReader(fh))
    fill_card_names(root, cards)
    per_list, per_card = Counter(r[0] for r in rows), Counter(c["team"] for c in cards)
    for team in sorted(per_card):
        if per_list[team] != per_card[team]:
            print(f"  WARNING roster_list.csv has {per_list[team]} {team} rows, cards.csv {per_card[team]}")
    values = read_card_values(root, cards)
    keys = ["age", "height", "weight", "experience"]
    out_cols = list(cards[0].keys()) + ["vision_" + k for k in keys] + ["vision_disagrees"]
    out = []
    for i, c in enumerate(cards):
        g = values.get(i)
        disagree = []
        row = dict(c)
        for k in keys:
            row["vision_" + k] = c[k]
            gv = _card_value(k, getattr(g, k)) if g else ""
            row[k] = gv
            if c[k] and gv and c[k] != gv:
                disagree.append(k)
        if not g:
            row["flags"] = ";".join(filter(None, [c["flags"], "no_gemini_values"]))
        row["vision_disagrees"] = ";".join(disagree)
        out.append(row)
    with open(os.path.join(root, "roster_cards.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=out_cols)
        w.writeheader()
        w.writerows(out)
    n_dis = sum(1 for r in out if r["vision_disagrees"])
    print(f"roster_cards.csv: {len(out)} cards; Gemini and Vision disagree on {n_dis}")


# ------------------------------------------------------------ prospects ----
class HighlightedProspect(BaseModel):
    projected_pick: str
    full_name: str
    position: str
    college: str
    height: str
    weight: str
    age: str
    overall_rating: str
    archetype: str = ""
    notes: str = ""


PROSPECT_PROMPT = """This is a screenshot from the Madden NFL Draft Board / College Prospects screen.
Find the single HIGHLIGHTED / SELECTED prospect row (the row with the active highlight bar/cursor, which displays the player's full name, height, and weight).
Return the highlighted prospect's details:
- projected_pick: projected pick rank or draft slot number (e.g. "1")
- full_name: player's full name as shown on the highlighted row/card (e.g. "Dante Moore")
- position: position abbreviation (e.g. "QB", "OT", "CB")
- college: college or university name (e.g. "Oregon")
- height: height (e.g. "6'3\"")
- weight: weight in lbs (e.g. "210")
- age: age in years (e.g. "21")
- overall_rating: scouted overall rating, grade, or talent (e.g. "83")
- archetype: archetype or player style if shown, else ""
- notes: scouting notes or traits if visible, else ""
"""


def extract_prospects(root):
    """prospects/prospect_*.png -> prospects.csv (reads highlighted row via Apple Vision OCR with Gemini fallback)."""
    from pipeline.extract_prospects_ocr import find_prospect_frames
    paths = find_prospect_frames(root)
    if not paths:
        return

    # Try local Apple Vision OCR first
    try:
        from pipeline.extract_prospects_ocr import extract_prospects_ocr, HAS_APPLE_VISION
        if HAS_APPLE_VISION:
            csv_path = extract_prospects_ocr(root)
            if csv_path and os.path.exists(csv_path):
                _sync_prospects_board(csv_path)
                return
    except Exception as e:
        print(f"  OCR extraction skipped or failed ({e}); falling back to Gemini vision...")

    print(f"Extracting {len(paths)} highlighted prospect rows via Gemini...")
    prefetch(paths)

    def read_one(path):
        return path, _generate([PROSPECT_PROMPT, prepare_image(path)], HighlightedProspect, os.path.basename(path))

    results = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(read_one, p) for p in paths]
        for fut in as_completed(futures):
            try:
                p, res = fut.result()
                results[p] = res
            except Exception as e:
                print(f"  ERROR extracting prospect: {e}")

    out_cols = ["PROJECTED_PICK", "FULL_NAME", "POSITION", "COLLEGE", "HEIGHT", "WEIGHT", "AGE", "OVR", "ARCHETYPE", "NOTES"]
    out_rows = []
    for p in paths:
        r = results.get(p)
        if r:
            out_rows.append([
                r.projected_pick, r.full_name, r.position, r.college,
                r.height, r.weight, r.age, r.overall_rating,
                r.archetype, r.notes
            ])

    out_path = os.path.join(root, "prospects.csv")
    write_csv(out_path, out_cols, out_rows)
    print(f"Wrote {len(out_rows)} prospects to {out_path}")
    _sync_prospects_board(out_path)


def _sync_prospects_board(csv_path):
    """Synchronize extracted prospects.csv with canonical draft board storage."""
    if not csv_path or not os.path.exists(csv_path):
        return
    import shutil
    try:
        from db.db_handler import DatabaseHandler
        db = DatabaseHandler()
        season, _ = db.get_current_league_state()
        draft_season = season + 1
    except Exception:
        draft_season = 2036

    dest_draft = os.path.join("data", "draft", f"{draft_season}_prospects.csv")
    os.makedirs(os.path.dirname(dest_draft), exist_ok=True)
    if os.path.abspath(csv_path) != os.path.abspath(dest_draft):
        shutil.copyfile(csv_path, dest_draft)
        print(f"Synced {csv_path} -> {dest_draft}")


# ---------------------------------------------------------------- games ----
GAME_TABLE_BOX = (0, 0.37, 1, 0.88)       # a game's player stats: chips + table, not the player banner
BOX_SCORE_BOX = (0.05, 0.18, 0.95, 0.83)  # scoreboard + team-stat rows


class BoxRow(BaseModel):
    label: str
    away: str
    home: str


class BoxScore(BaseModel):
    away_team: str              # abbreviation on the left of the scoreboard, e.g. DET
    home_team: str
    away_quarters: list[str]    # 1ST 2ND 3RD 4TH OT, as shown
    home_quarters: list[str]
    away_total: str
    home_total: str
    rows: list[BoxRow]


BOX_PROMPT = """This is a Madden NFL box score. The scoreboard at the top shows two teams: the left
team (away) and the right team (home), each with points per quarter (1ST, 2ND, 3RD, 4TH, OT) and
a TOTAL. Below it, each row is a team stat: the left number is the left team's, the label is in
the middle, the right number is the right team's. Return the two team abbreviations, the
quarter scores and totals, and every visible stat row exactly as shown (e.g. "37:45")."""

GAME_NOTE = """
This is one game's player stats; the TEAM column shows each player's team abbreviation. Ignore
the player photo/banner above the table."""


def _is_game(path):
    return f"{os.sep}games{os.sep}" in path


def read_box(path):
    if path not in _cache:
        b = _generate([BOX_PROMPT, prepare_image(path, BOX_SCORE_BOX)], BoxScore, os.path.basename(path))
        _cache[path] = b.model_dump()
    return _cache[path]


HIGHLIGHTS_BOX = (0.05, 0.08, 0.95, 0.92)


class HighlightPlay(BaseModel):
    quarter: str
    clock: str
    down_dist: str = ""
    play: str
    play_type: str = "PLAY"
    scoring: bool = False
    points: int = 0


class HighlightPage(BaseModel):
    plays: list[HighlightPlay]


HIGHLIGHT_PROMPT = """Extract all plays visible in order from top to bottom on this Madden game highlights screen.
Return every play with:
- quarter: e.g. 'Q4', 'Q3', 'Q2', 'Q1', 'OT' (from the active quarter indicated by the underline on the scoreboard)
- clock: game clock time, e.g. '0:18'
- down_dist: e.g. '1ST & 10 ON PIT 18' (or '' if kickoff/extra point)
- play: exact play description
- play_type: e.g. 'RUSH', 'PASS', 'TOUCHDOWN', 'FIELD_GOAL', 'EXTRA_POINT', 'TWO_POINT', 'SAFETY', 'INTERCEPTION', 'FUMBLE', 'SACK', 'KICKOFF', 'PUNT', 'PENALTY'
- scoring: true if points were scored
- points: 6 for TD, 3 for FG, 1 for XP, 2 for 2PT/Safety, 0 otherwise
"""


def read_highlights(path):
    if path not in _cache:
        hp = _generate([HIGHLIGHT_PROMPT, prepare_image(path, HIGHLIGHTS_BOX)], HighlightPage, os.path.basename(path))
        _cache[path] = [p.model_dump() for p in hp.plays]
    return _cache[path]


def extract_highlights(hdir):
    """Extract and chronologically order all highlights from highlights/*.png."""
    paths = sorted(glob.glob(os.path.join(hdir, "*.png")) + glob.glob(os.path.join(hdir, "*.webp")))
    if not paths:
        return []
    all_plays, seen = [], set()
    for p in paths:
        try:
            plays = read_highlights(p)
            for pl in plays:
                key = (pl.get("quarter", "").upper(), pl.get("clock"), pl.get("play"))
                if key not in seen:
                    seen.add(key)
                    all_plays.append(pl)
        except Exception as e:
            print(f"  WARNING extracting {p}: {e}")

    q_rank = {"Q1": 1, "1ST": 1, "Q2": 2, "2ND": 2, "Q3": 3, "3RD": 3, "Q4": 4, "4TH": 4, "OT": 5}
    def sort_key(pl):
        q = q_rank.get(pl.get("quarter", "").upper(), 9)
        clock_s = 0
        try:
            parts = pl.get("clock", "0:00").split(":")
            clock_s = int(parts[0]) * 60 + int(parts[1])
        except Exception:
            pass
        return (q, -clock_s)

    all_plays.sort(key=sort_key)
    return all_plays


def extract_games(root):
    """games/<NN>_<away>_at_<home>/: box_top/box_bottom -> box_score.csv (label, away, home);
    stats/*.png -> stats_<category>.csv. Week-level: game_scores.csv (one row a game) and
    game_stats_<category>.csv (GAME column + the category's columns)."""
    game_dirs = sorted(glob.glob(os.path.join(root, "games", "[0-9]*_*")))
    if not game_dirs:
        return
    prefetch(sorted(p for d in game_dirs for p in glob.glob(os.path.join(d, "stats", "*.png"))))
    scores, week = [], {}
    for d in game_dirs:
        game = os.path.basename(d)
        rows, b = OrderedDict(), None
        for name in ("box_top.png", "box_bottom.png"):
            if os.path.exists(os.path.join(d, name)):
                b = read_box(os.path.join(d, name))
                for r in b["rows"]:
                    rows.setdefault(r["label"].upper(), (r["away"], r["home"]))
        if b:
            write_csv(os.path.join(d, "box_score.csv"), ["LABEL", b["away_team"], b["home_team"]],
                      [[k, a, h] for k, (a, h) in rows.items()])
            q = lambda xs: (xs + [""] * 5)[:5]

            def adds_up(qs, total):
                try:
                    return sum(int(x or 0) for x in qs) == int(total)
                except ValueError:
                    return False
            # a light team colour can wash out quarter digits (Raiders silver, Oct 14): the total
            # stays right, so flag the quarters instead of guessing them
            ok = adds_up(b["away_quarters"], b["away_total"]) and adds_up(b["home_quarters"], b["home_total"])
            scores.append([game, b["away_team"], *q(b["away_quarters"]), b["away_total"],
                           b["home_team"], *q(b["home_quarters"]), b["home_total"], "y" if ok else "n"])
            print(f"{game}: {b['away_team']} {b['away_total']} - {b['home_total']} {b['home_team']}, {len(rows)} box rows")
        groups = {}
        for p in glob.glob(os.path.join(d, "stats", "*.png")):
            groups.setdefault(os.path.basename(p).rsplit("_", 1)[0], []).append(p)
        for key, paths in sorted(groups.items()):
            if key.endswith("_right"):
                continue
            if key + "_right" in groups:
                cols, trows = merge_wide(paths, groups[key + "_right"], f"{game} {key}")
            else:
                cols, trows = merge(paths, f"{game} {key}")
            cat = key.split("_", 1)[1]
            write_csv(os.path.join(d, f"stats_{cat}.csv"), cols, trows)
            wc, wr = week.setdefault(cat, [None, []])
            week[cat][0] = wc or cols
            week[cat][1] += [[game] + list(r) for r in trows]
        hdir = os.path.join(d, "highlights")
        if os.path.isdir(hdir):
            hl = extract_highlights(hdir)
            if hl:
                import json
                with open(os.path.join(d, "highlights.json"), "w") as fh:
                    json.dump(hl, fh, indent=2)
                print(f"{game}: extracted {len(hl)} highlights")
    hdr = ["GAME", "AWAY", "A1", "A2", "A3", "A4", "AOT", "ATOTAL", "HOME", "H1", "H2", "H3", "H4", "HOT", "HTOTAL",
           "QUARTERS_OK"]
    write_csv(os.path.join(root, "game_scores.csv"), hdr, scores)
    for cat, (cols, rows) in sorted(week.items()):
        write_csv(os.path.join(root, f"game_stats_{cat}.csv"), ["GAME"] + list(cols), rows)


def write_csv(path, columns, rows):
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(columns)
        w.writerows(rows)


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    root = sys.argv[1]
    try:
        from pipeline.extract_prospects_ocr import find_prospect_frames
        has_prosp_lines = bool(find_prospect_frames(root))
    except ImportError:
        has_prosp_lines = False
    legacy_prosp = []
    if not has_prosp_lines:
        legacy_prosp = [p for p in glob.glob(os.path.join(root, "prospects", "*.png"))
                        if not os.path.basename(p).startswith("prospect_")]
    prefetch(sorted(glob.glob(os.path.join(root, "stats", "*.png")) +
                    glob.glob(os.path.join(root, "team_stats", "*.png")) +
                    glob.glob(os.path.join(root, "schedule", "*.png")) +
                    glob.glob(os.path.join(root, "schedule_next", "*.png")) +
                    glob.glob(os.path.join(root, "season_schedule", "*.png")) +
                    legacy_prosp +
                    glob.glob(os.path.join(root, "stats_scope", "*.png"))))
    groups = {}
    for p in glob.glob(os.path.join(root, "stats", "*.png")):
        key = os.path.basename(p).rsplit("_", 1)[0]          # e.g. 01_passing
        groups.setdefault(key, []).append(p)
    for key, paths in sorted(groups.items()):
        if key.endswith("_right"):
            continue
        if key + "_right" in groups:
            both = paths + groups[key + "_right"]
            cols, rows = vote_misreads(key, lambda: merge_wide(paths, groups[key + "_right"], key), both, ["NAME", "POS"])
        else:
            cols, rows = vote_misreads(key, lambda: merge(paths, key), paths, ["NAME", "POS"])
        write_csv(os.path.join(root, f"stats_{key.split('_', 1)[1]}.csv"), cols, rows)

    # team-scoped Season Stats (--only stats --scope TEAM): stats_scope/<team>_<category>_NNN.png
    scoped = {}
    for p in glob.glob(os.path.join(root, "stats_scope", "*.png")):
        scoped.setdefault(os.path.basename(p).rsplit("_", 1)[0], []).append(p)
    for key, paths in sorted(scoped.items()):
        if key.endswith("_right"):
            continue
        if key + "_right" in scoped:
            both = paths + scoped[key + "_right"]
            cols, rows = vote_misreads(key, lambda: merge_wide(paths, scoped[key + "_right"], key), both, ["NAME", "POS"])
        else:
            cols, rows = vote_misreads(key, lambda: merge(paths, key), paths, ["NAME", "POS"])
        write_csv(os.path.join(root, f"stats_scope_{key}.csv"), cols, rows)

    team = {}
    for p in glob.glob(os.path.join(root, "team_stats", "*.png")):
        team.setdefault(os.path.basename(p).rsplit("_", 1)[0], []).append(p)   # e.g. team_penalties
    for key, paths in sorted(team.items()):
        cols, rows = merge(paths, key)
        write_csv(os.path.join(root, f"{key}.csv"), cols, rows)

    sched = glob.glob(os.path.join(root, "schedule", "*.png"))
    if sched:
        cols, rows = merge(sched, "schedule")
        write_csv(os.path.join(root, "schedule.csv"), cols, rows)
    nxt = glob.glob(os.path.join(root, "schedule_next", "*.png"))
    if nxt:
        cols, rows = merge(nxt, "next round")
        write_csv(os.path.join(root, "schedule_next.csv"), cols, rows)

    season = glob.glob(os.path.join(root, "season_schedule", "*.png"))
    if season:
        cols, rows = merge(season, "season schedule")      # rows carry their WEEK column
        write_csv(os.path.join(root, "season_schedule.csv"), cols, rows)

    if has_prosp_lines:
        extract_prospects(root)
    else:
        prosp = glob.glob(os.path.join(root, "prospects", "*.png"))
        if prosp:
            cols, rows = merge(prosp, "prospects")
            p_out = os.path.join(root, "prospects.csv")
            write_csv(p_out, cols, rows)
            _sync_prospects_board(p_out)

    extract_roster(root)
    extract_games(root)
    if os.path.isdir(os.path.join(root, "injuries")):
        try:
            from pipeline.extract_injuries_ocr import extract_injuries_ocr
            extract_injuries_ocr(root)
        except Exception as e:
            print(f"Error extracting injuries via Apple Vision: {e}")
    if METER is not None:
        try:
            from pipeline.load_capture import detect_mode
            mode = detect_mode(root)
        except Exception:
            mode = None
        t = METER.record("extract", model=MODEL, capture_folder=os.path.basename(os.path.normpath(root)), mode=mode)
        print(f"Gemini: {t['calls']} calls, {t['input_tokens']:,} input ({t['cached_tokens']:,} cached), "
              f"{t['output_tokens']:,} output, {t['thinking_tokens']:,} thinking tokens")
    print("Done.")


if __name__ == "__main__":
    main()
