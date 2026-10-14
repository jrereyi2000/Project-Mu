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
  season_schedule.csv    --only season-schedule: every week 1-18 (same columns as schedule.csv)
  roster_list.csv        --only roster list pages: TEAM + NAME, POS, AGE, OVR, MOR, ... (icons blank)
  roster_cards.csv       roster/cards.csv (Apple Vision) with AGE/HT/WT/EXP re-read by Gemini from
                         the card crops, 20 per request; vision_* keep Vision's values and
                         vision_disagrees names the fields where the two differ
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


def _get_client():
    global _client
    with _client_lock:
        if _client is None:
            _client = genai.Client(api_key=_api_key())
    return _client


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
        for r in t["rows"]:
            k = tuple(r[i] if i < len(r) else "" for i in idx) if idx else tuple(r)
            if k not in rows:                 # pages overlap; keep first copy
                rows[k] = r
            elif key_cols and list(rows[k]) != list(r):
                # the same row read two ways on overlapping pages: say so, the values differ
                diff = [f"{c} {a!r}/{b!r}" for c, a, b in zip(columns, rows[k], r) if a != b]
                print(f"  CONFLICT {label} {' '.join(k)}: {', '.join(diff)} (kept the first)")
        print(f"  {os.path.basename(p)}: {len(t['rows'])} rows")
    print(f"{label}: {len(rows)} unique rows")
    return columns, [list(r) for r in rows.values()] if key_cols else list(rows)


def merge_wide(left_paths, right_paths, label):
    """Wide tables: NAME/POS scroll away, so each right-hand page was captured at the same
    scroll position as its left-hand page. Pair pages by number and rows by position."""
    rights = {os.path.basename(p).rsplit("_", 1)[1]: p for p in right_paths}
    columns, rows = None, OrderedDict()
    for lp in sorted(left_paths):
        num = os.path.basename(lp).rsplit("_", 1)[1]
        lt = read_table(lp)
        rt = read_table(rights[num]) if num in rights else {"columns": [], "rows": []}
        lcols = lt["columns"]
        keep = [i for i, c in enumerate(rt["columns"]) if c.upper() not in {x.upper() for x in lcols}]
        if columns is None:
            columns = list(lcols) + [rt["columns"][i] for i in keep]
        if len(lt["rows"]) != len(rt["rows"]):
            print(f"  WARNING {os.path.basename(lp)}: {len(lt['rows'])} left rows vs "
                  f"{len(rt['rows'])} right rows; pairing the first {min(len(lt['rows']), len(rt['rows']))}")
        # Columns visible in both halves (e.g. SACK, XPA) must agree row by row; if they
        # don't, the two screenshots weren't taken at the same scroll position.
        lup = [c.upper() for c in lcols]
        shared = [(lup.index(c.upper()), j) for j, c in enumerate(rt["columns"]) if c.upper() in lup]
        bad = 0
        for i, lr in enumerate(lt["rows"]):
            rr = rt["rows"][i] if i < len(rt["rows"]) else []
            if rr and any(li < len(lr) and rj < len(rr) and lr[li] != rr[rj] for li, rj in shared):
                bad += 1
            rows.setdefault(tuple(lr) + tuple(rr[j] if j < len(rr) else "" for j in keep), None)
        if bad:
            print(f"  WARNING {os.path.basename(lp)}: {bad} rows disagree on shared columns "
                  f"{[rt['columns'][j] for _, j in shared]}; check this page pair")
        print(f"  {os.path.basename(lp)} + right: {len(lt['rows'])} rows")
    print(f"{label}: {len(rows)} unique rows")
    return columns, list(rows)


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


def write_csv(path, columns, rows):
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(columns)
        w.writerows(rows)


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    root = sys.argv[1]
    prefetch(sorted(glob.glob(os.path.join(root, "stats", "*.png")) +
                    glob.glob(os.path.join(root, "team_stats", "*.png")) +
                    glob.glob(os.path.join(root, "schedule", "*.png")) +
                    glob.glob(os.path.join(root, "season_schedule", "*.png"))))
    groups = {}
    for p in glob.glob(os.path.join(root, "stats", "*.png")):
        key = os.path.basename(p).rsplit("_", 1)[0]          # e.g. 01_passing
        groups.setdefault(key, []).append(p)
    for key, paths in sorted(groups.items()):
        if key.endswith("_right"):
            continue
        if key + "_right" in groups:
            cols, rows = merge_wide(paths, groups[key + "_right"], key)
        else:
            cols, rows = merge(paths, key)
        write_csv(os.path.join(root, f"stats_{key.split('_', 1)[1]}.csv"), cols, rows)

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

    season = glob.glob(os.path.join(root, "season_schedule", "*.png"))
    if season:
        cols, rows = merge(season, "season schedule")      # rows carry their WEEK column
        write_csv(os.path.join(root, "season_schedule.csv"), cols, rows)

    extract_roster(root)
    print("Done.")


if __name__ == "__main__":
    main()
