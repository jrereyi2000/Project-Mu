"""Real NFL awards 1999-2025 (docs/in_progress/REAL_NFL_HISTORY_PLAN.md §2.5, as built: Wikipedia fallback).

Pro-Football-Reference refuses automated fetches (HTTP 403 Cloudflare challenge, spike S21), so the CSV is built
from Wikipedia's API (action=parse) per the plan's fallback, and Pro Bowl is dropped (Q6):
  - the seven single winners: one award page each (MVP, OPOY, DPOY, OROY and DROY share the rookie page, COTY,
    Super Bowl MVP), PAGES;
  - AP First and Second Team All-Pro: `<season> All-Pro Team`, AP selections only (PFWA/TSN dropped).
Pages are cached in data/history/.cache/wiki_<page>.json, WIKI_DELAY_S apart (Wikipedia rate-limits fast clients).

  python3 -m engines.nfl_history.awards build [--seasons 1999-2025] [--refresh] [--out PATH]

Pure parsers: parse_tables(html), parse_award_page(html, award_type), parse_allpro(html, season). Tables inside
HTML comments are read too. The CSV (AWARDS_CSV, committed) has columns CSV_COLUMNS; gsis_id is resolved at build
time from nflverse (name + team + season, then name + season), blank when it can't be settled; pfr_id stays blank
(Wikipedia has none). `load` keeps gsis_id only when nfl_players has it.
"""
import argparse
import csv
import html as htmllib
import json
import os
import re
import sys
import unicodedata
import urllib.parse
from html.parser import HTMLParser

from engines.nfl_history import fetch
from engines.nfl_history.teams import UnknownTeam, mu_abbr

NFL_AWARD_TYPES = ('MVP', 'OPOY', 'DPOY', 'OROY', 'DROY', 'COTY', 'SBMVP', 'First Team All-Pro',
                   'Second Team All-Pro', 'Pro Bowl')
SINGLE_AWARDS = ('MVP', 'OPOY', 'DPOY', 'OROY', 'DROY', 'COTY', 'SBMVP')
# award -> (Wikipedia page, caption the winners table must contain, or None)
PAGES = {
    "MVP": ("AP_NFL_Most_Valuable_Player", "Most Valuable Player winners"),
    "OPOY": ("AP_NFL_Offensive_Player_of_the_Year", None),
    "DPOY": ("AP_NFL_Defensive_Player_of_the_Year", None),
    "OROY": ("AP_NFL_Offensive_Rookie_of_the_Year", "Offensive Rookie"),
    "DROY": ("AP_NFL_Defensive_Rookie_of_the_Year", "Defensive Rookie"),
    "COTY": ("AP_NFL_Coach_of_the_Year", "Coach of the Year winners"),
    "SBMVP": ("Super_Bowl_Most_Valuable_Player_Award", "Super Bowl Most Valuable Players"),
}
WIKI_API = "https://en.wikipedia.org/w/api.php?action=parse&prop=text&format=json&formatversion=2&redirects=1&page="
WIKI_DELAY_S = 5.0
FIRST_SEASON, LAST_SEASON = 1999, 2025
AWARDS_CSV = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "history",
                                           "nfl_awards_1999_2025.csv"))
CSV_COLUMNS = ("season", "award_type", "gsis_id", "pfr_id", "player_name", "coach_name", "team_abbr", "position")

POSITIONS = {
    "quarterback": "QB", "running back": "RB", "halfback": "RB", "fullback": "FB", "wide receiver": "WR",
    "tight end": "TE", "tackle": "T", "offensive tackle": "T", "guard": "G", "offensive guard": "G",
    "center": "C", "centre": "C", "offensive lineman": "OL", "defensive end": "DE", "defensive tackle": "DT",
    "edge rusher": "EDGE", "edge": "EDGE", "interior lineman": "DL", "interior defensive lineman": "DL",
    "defensive lineman": "DL", "linebacker": "LB", "outside linebacker": "OLB", "inside linebacker": "ILB",
    "middle linebacker": "MLB", "off-ball linebacker": "LB", "cornerback": "CB", "safety": "S",
    "free safety": "FS", "strong safety": "SS", "defensive back": "DB", "kicker": "K", "placekicker": "K",
    "punter": "P", "kick returner": "KR", "punt returner": "PR", "return specialist": "RS",
    "kick returner / punt returner": "RS", "all-purpose": "APR", "all-purpose player": "APR",
    "left tackle": "LT", "right tackle": "RT", "left guard": "LG", "right guard": "RG",
    "interior lineman/defensive tackle": "DT", "defensive tackle/interior lineman": "DT",
    "slot cornerback": "SCB", "nickel back": "SCB", "nickelback": "SCB",
    "long snapper": "LS", "special teams": "ST", "special teamer": "ST", "flex": "FLEX",
}
# team names as Wikipedia writes them (full names, nicknames, cities) -> nflverse-style code (mu_abbr maps it)
TEAM_NAMES = {
    "arizona cardinals": "ARI", "atlanta falcons": "ATL", "baltimore ravens": "BAL", "buffalo bills": "BUF",
    "carolina panthers": "CAR", "chicago bears": "CHI", "cincinnati bengals": "CIN", "cleveland browns": "CLE",
    "dallas cowboys": "DAL", "denver broncos": "DEN", "detroit lions": "DET", "green bay packers": "GB",
    "houston texans": "HOU", "indianapolis colts": "IND", "jacksonville jaguars": "JAX",
    "kansas city chiefs": "KC", "las vegas raiders": "LV", "oakland raiders": "OAK",
    "los angeles chargers": "LAC", "san diego chargers": "SD", "los angeles rams": "LA", "st. louis rams": "STL",
    "st louis rams": "STL", "miami dolphins": "MIA", "minnesota vikings": "MIN", "new england patriots": "NE",
    "new orleans saints": "NO", "new york giants": "NYG", "new york jets": "NYJ",
    "philadelphia eagles": "PHI", "pittsburgh steelers": "PIT", "san francisco 49ers": "SF",
    "seattle seahawks": "SEA", "tampa bay buccaneers": "TB", "tennessee titans": "TEN",
    "washington redskins": "WAS", "washington football team": "WAS", "washington commanders": "WAS",
    "washington": "WAS",
}
_NICK = {"cardinals": "ARI", "falcons": "ATL", "ravens": "BAL", "bills": "BUF", "panthers": "CAR", "bears": "CHI",
         "bengals": "CIN", "browns": "CLE", "cowboys": "DAL", "broncos": "DEN", "lions": "DET", "packers": "GB",
         "texans": "HOU", "colts": "IND", "jaguars": "JAX", "chiefs": "KC", "raiders": "LV", "chargers": "LAC",
         "rams": "LA", "dolphins": "MIA", "vikings": "MIN", "patriots": "NE", "saints": "NO", "giants": "NYG",
         "jets": "NYJ", "eagles": "PHI", "steelers": "PIT", "49ers": "SF", "seahawks": "SEA", "buccaneers": "TB",
         "titans": "TEN", "redskins": "WAS", "commanders": "WAS", "football team": "WAS"}
_CITY = {"arizona": "ARI", "atlanta": "ATL", "baltimore": "BAL", "buffalo": "BUF", "carolina": "CAR",
         "chicago": "CHI", "cincinnati": "CIN", "cleveland": "CLE", "dallas": "DAL", "denver": "DEN",
         "detroit": "DET", "green bay": "GB", "houston": "HOU", "indianapolis": "IND", "jacksonville": "JAX",
         "kansas city": "KC", "las vegas": "LV", "oakland": "OAK", "san diego": "SD", "st. louis": "STL",
         "miami": "MIA", "minnesota": "MIN", "new england": "NE", "new orleans": "NO", "philadelphia": "PHI",
         "pittsburgh": "PIT", "san francisco": "SF", "seattle": "SEA", "tampa bay": "TB", "tennessee": "TEN",
         "washington": "WAS", "ny giants": "NYG", "ny jets": "NYJ", "la rams": "LA", "la chargers": "LAC",
         "los angeles rams": "LA", "los angeles chargers": "LAC", "l.a. rams": "LA", "l.a. chargers": "LAC"}


# ---------- HTML tables ----------

class _TableParser(HTMLParser):
    """Collects every <table> as {"caption", "class", "rows": [[cell, ...]]}; a cell is {"text", "links", "th",
    "rowspan", "colspan"}. Nested tables are collected separately and left out of their parent's cells."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self.stack, self.caption, self.skip, self.spans = [], [], None, 0, []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("sup", "style", "script"):
            self.skip += 1
            return
        if tag == "span":
            hidden = "display:none" in (a.get("style") or "").replace(" ", "") or "sortkey" in (a.get("class") or "")
            self.spans.append(hidden)
            self.skip += hidden
            return
        if tag == "table":
            self.stack.append({"caption": "", "class": a.get("class") or "", "rows": [], "_cell": None})
            self.tables.append(self.stack[-1])
        elif not self.stack:
            return
        elif tag == "caption":
            self.caption = []
        elif tag == "tr":
            self.stack[-1]["rows"].append([])
        elif tag in ("td", "th"):
            t = self.stack[-1]
            if not t["rows"]:
                t["rows"].append([])
            c = {"text": [], "links": [], "th": tag == "th", "rowspan": _span(a.get("rowspan")),
                 "colspan": _span(a.get("colspan"))}
            t["rows"][-1].append(c)
            t["_cell"] = c
        elif tag == "br" and self.stack[-1]["_cell"] is not None:
            self.stack[-1]["_cell"]["text"].append("\n")
        elif tag == "a" and self.stack[-1]["_cell"] is not None:
            self.stack[-1]["_cell"]["links"].append(a.get("title") or "")

    def handle_endtag(self, tag):
        if tag in ("sup", "style", "script"):
            self.skip = max(0, self.skip - 1)
            return
        if tag == "span":
            if self.spans and self.spans.pop():
                self.skip = max(0, self.skip - 1)
            return
        if not self.stack:
            return
        if tag == "table":
            t = self.stack.pop()
            t.pop("_cell", None)
            for row in t["rows"]:
                for c in row:
                    c["text"] = _clean("".join(c["text"]))
        elif tag == "caption" and self.caption is not None:
            self.stack[-1]["caption"] = _clean("".join(self.caption))
            self.caption = None
        elif tag in ("td", "th"):
            self.stack[-1]["_cell"] = None

    def handle_data(self, data):
        if self.skip or not self.stack:
            return
        if self.caption is not None:
            self.caption.append(data)
        elif self.stack[-1]["_cell"] is not None:
            self.stack[-1]["_cell"]["text"].append(data)


def _span(v):
    try:
        return max(1, int(str(v).strip().rstrip(";")))
    except (TypeError, ValueError):
        return 1


def _clean(s):
    s = s.replace("\xa0", " ")
    lines = [re.sub(r"[ \t\r\f\v]+", " ", x).strip() for x in s.split("\n")]
    return "\n".join(x for x in lines if x)


def _uncomment(html):
    """Tables hidden in HTML comments (Pro-Football-Reference does this) are made visible."""
    return re.sub(r"<!--(.*?)-->", lambda m: m.group(1) if "<table" in m.group(1) else "", html, flags=re.S)


def parse_tables(html):
    """Every table as {"caption", "class", "grid"}: grid = rows of cells with rowspan/colspan expanded."""
    p = _TableParser()
    p.feed(_uncomment(html))
    out = []
    for t in p.tables:
        grid, carry = [], {}
        for row in t["rows"]:
            cells, col = [], 0
            pending = list(row)
            while pending or col in carry:
                if col in carry:
                    c, left = carry[col]
                    cells.append(c)
                    if left <= 1:
                        del carry[col]
                    else:
                        carry[col] = (c, left - 1)
                    col += 1
                    continue
                c = pending.pop(0)
                for _ in range(c["colspan"]):
                    cells.append(c)
                    if c["rowspan"] > 1:
                        carry[col] = (c, c["rowspan"] - 1)
                    col += 1
            grid.append(cells)
        out.append({"caption": t["caption"], "class": t["class"], "grid": grid})
    return out


# ---------- names, teams, positions ----------

def norm_name(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[’'`.]", "", s).replace("-", " ")
    s = re.sub(r"[^a-z ]", " ", s)
    return collapse_initials([w for w in s.split() if w not in ("jr", "sr", "ii", "iii", "iv", "v")])


def collapse_initials(words):
    """Runs of single letters join up: ["j", "j", "watt"] -> "jj watt" (nflverse writes "JJ Watt")."""
    out, run = [], ""
    for w in words:
        if len(w) == 1:
            run += w
            continue
        if run:
            out.append(run)
            run = ""
        out.append(w)
    if run:
        out.append(run)
    return " ".join(out)


def _strip_marks(s):
    s = re.sub(r"\(\d+\)", "", s or "")
    s = re.sub(r"[†‡§*^~#¤+]", "", s)
    return re.sub(r"\s+", " ", s).strip(" ,")


def team_code(text, links=()):
    """nflverse-style code for a Wikipedia team text (full name, nickname or city) or its link titles; None."""
    for cand in list(links) + [text]:
        t = re.sub(r"^\d{4} ", "", (cand or "").strip()).lower()
        t = re.sub(r" season$", "", t).strip(" ,")
        if t in TEAM_NAMES:
            return TEAM_NAMES[t]
    t = (text or "").strip().lower().strip(" ,")
    if t in _CITY:
        return _CITY[t]
    for nick, code in _NICK.items():
        if t.endswith(nick):
            return code
    return None


def position_code(text):
    t = _strip_marks(text).lower()
    t = re.sub(r"\s*\(.*?\)", "", t).strip()
    if t in POSITIONS:
        return POSITIONS[t]
    if "/" in t and t.split("/")[0].strip() in POSITIONS:
        return POSITIONS[t.split("/")[0].strip()]
    if t.upper() in {v for v in POSITIONS.values()} or re.fullmatch(r"[a-z]{1,4}", t):
        return t.upper()
    return None


# ---------- single winners ----------

def _season_of(text, award_type):
    m = re.search(r"\b(19|20)\d{2}\b", text or "")
    if not m:
        return None
    year = int(m.group(0))
    return year - 1 if award_type == "SBMVP" else year   # Super Bowl tables list the game's calendar year


def _header_index(grid):
    """(header row index, {name: column}) for the first row that names Season/Year and Player/Coach/Winner."""
    for i, row in enumerate(grid[:4]):
        names = [_strip_marks(re.sub(r"\[.*?\]", "", c["text"])).lower() for c in row]
        cols = {}
        for j, n in enumerate(names):
            for key, words in (("season", ("season", "year")), ("player", ("player", "winner", "coach")),
                               ("position", ("position",)), ("team", ("team",))):
                if key not in cols and any(n.startswith(w) for w in words):
                    cols[key] = j
        if "season" in cols and "player" in cols:
            return i, cols
    return None, {}


def parse_award_page(html, award_type, caption=None, first=FIRST_SEASON, last=LAST_SEASON):
    """Winners of one single award from its Wikipedia page: [{season, award_type, player_name | coach_name,
    team_code, position}], seasons first..last, in page order."""
    if award_type not in SINGLE_AWARDS:
        raise ValueError(award_type)
    for t in parse_tables(html):
        if caption and caption.lower() not in t["caption"].lower():
            continue
        if "wikitable" not in t["class"]:
            continue
        h, cols = _header_index(t["grid"])
        if h is None:
            continue
        out, seen = [], set()
        for row in t["grid"][h + 1:]:
            if len(row) <= max(cols.values()):
                continue
            season = _season_of(row[cols["season"]]["text"], award_type)
            if season is None or not first <= season <= last:
                continue
            names = [_strip_marks(x) for x in row[cols["player"]]["text"].split("\n")]
            team_cell = row[cols["team"]] if "team" in cols else None
            team_lines = team_cell["text"].split("\n") if team_cell else []
            team_links = [x for x in team_cell["links"] if x] if team_cell else []
            pos = position_code(row[cols["position"]]["text"].split("\n")[0]) if "position" in cols else None
            for i, name in enumerate(n for n in names if n):   # co-winners share a cell, one per line
                if (season, name) in seen:
                    continue
                seen.add((season, name))
                code = None
                if team_lines:
                    k = i if i < len(team_lines) else 0
                    code = team_code(team_lines[k], team_links[k:k + 1] if len(team_links) == len(team_lines)
                                     else team_links[:1] if len(team_lines) == 1 else [])
                r = {"season": season, "award_type": award_type, "team_code": code,
                     "position": None if award_type == "COTY" else pos}
                r["coach_name" if award_type == "COTY" else "player_name"] = name
                out.append(r)
        if out:
            return out
    return []


# ---------- All-Pro ----------

_ENTRY = re.compile(r"^(?P<name>.+?),\s*(?P<team>[^()]+?)\s*\((?P<sel>[^)]*)\)[\s,;.]*$")


def _selections(sel):
    toks = [t.strip() for t in re.split(r"[,;]", sel)]
    first = any(re.fullmatch(r"AP(-t)?", t) for t in toks)
    second = any(re.fullmatch(r"AP-2t?", t) for t in toks)
    return first, second


def parse_allpro(html, season):
    """AP First and Second Team All-Pro selections of one season's `<season> All-Pro Team` page:
    [{season, award_type, player_name, team_code, position}]. Other selectors (PFWA, TSN) are dropped."""
    out, seen = [], set()
    for t in parse_tables(html):
        if "wikitable" not in t["class"]:
            continue
        for row in t["grid"]:
            if len(row) < 2 or row[0]["th"]:
                continue
            pos = position_code(row[0]["text"])
            if not pos:
                continue
            for cell in row[1:]:
                for line in cell["text"].split("\n"):
                    m = _ENTRY.match(line.strip())
                    if not m:
                        continue
                    first, second = _selections(m.group("sel"))
                    name = _strip_marks(m.group("name"))
                    code = team_code(m.group("team").strip())
                    for flag, kind in ((first, "First Team All-Pro"), (second, "Second Team All-Pro")):
                        if flag and (kind, pos, name) not in seen:
                            seen.add((kind, pos, name))
                            out.append({"season": int(season), "award_type": kind, "player_name": name,
                                        "team_code": code, "position": pos})
    return out


# ---------- gsis resolution ----------

class Resolver:
    """Resolves (name, season, team code) to a gsis_id from nflverse players and season lines (pure)."""

    def __init__(self, players, season_rows):
        self.name_of = {p["gsis_id"]: p for p in players}
        self.by_name = {}          # people with no stat line that season (offensive linemen, long snappers)
        for p in players:
            for n in self._names(p):
                self.by_name.setdefault(n, []).append(p)
        self.by_season = {}
        for r in season_rows:
            p = self.name_of.get(r["gsis_id"])
            for n in (self._names(p) if p else ()):
                self.by_season.setdefault((n, r["season"]), set()).add((r["gsis_id"], r["team_id"]))

    def resolve(self, name, season, code):
        cands = self.by_season.get((norm_name(name), season), set())
        if code:
            try:
                mu = mu_abbr(code, season)
            except UnknownTeam:
                mu = None
            on_team = {g for g, t in cands if t == mu}
            if len(on_team) == 1:
                return on_team.pop()
        ids = {g for g, _ in cands}
        if len(ids) == 1:
            return ids.pop()
        if ids:
            return None
        active = {p["gsis_id"] for p in self.by_name.get(norm_name(name), [])
                  if (p.get("rookie_season") or 0) <= season <= (p.get("last_season") or 0)}
        return active.pop() if len(active) == 1 else None

    @staticmethod
    def _names(p):
        names = {norm_name(p["display_name"])}
        if p.get("first_name") and p.get("last_name"):
            names.add(norm_name(f"{p['first_name']} {p['last_name']}"))
        names.discard("")
        return names


def to_csv_rows(raw, resolver=None):
    out = []
    for r in raw:
        gsis = None
        if resolver and r.get("player_name"):
            gsis = resolver.resolve(r["player_name"], r["season"], r.get("team_code"))
        out.append({"season": r["season"], "award_type": r["award_type"], "gsis_id": gsis or "", "pfr_id": "",
                    "player_name": r.get("player_name") or "", "coach_name": r.get("coach_name") or "",
                    "team_abbr": r.get("team_code") or "", "position": r.get("position") or ""})
    order = {a: i for i, a in enumerate(NFL_AWARD_TYPES)}
    out.sort(key=lambda r: (r["season"], order[r["award_type"]], r["position"], r["player_name"], r["coach_name"]))
    return out


def write_csv(rows, path=AWARDS_CSV):
    tmp = path + ".part"
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in CSV_COLUMNS})
    os.replace(tmp, path)


def read_csv(path=AWARDS_CSV):
    """CSV rows with season as int and blanks as None."""
    with open(path, newline="") as f:
        return [{k: (int(v) if k == "season" else (v or None)) for k, v in r.items()} for r in csv.DictReader(f)]


# ---------- build (network through the cache) ----------

def wiki_html(page, refresh=False):
    f = fetch.get(WIKI_API + urllib.parse.quote(page), f"wiki_{page}.json", refresh, delay=WIKI_DELAY_S)
    with open(f.path) as fh:
        return json.load(fh)["parse"]["text"]


def build(seasons=range(FIRST_SEASON, LAST_SEASON + 1), refresh=False, out=AWARDS_CSV, resolve=True):
    first, last = min(seasons), max(seasons)
    raw = []
    for award, (page, caption) in PAGES.items():
        raw += parse_award_page(wiki_html(page, refresh), award, caption, first, last)
    for s in seasons:
        raw += parse_allpro(wiki_html(f"{s}_All-Pro_Team", refresh), s)
    resolver = _resolver(seasons) if resolve else None
    rows = to_csv_rows(raw, resolver)
    write_csv(rows, out)
    return rows


def _resolver(seasons):
    from engines.nfl_history import nflverse
    files = {f.cache_name: f.path for f in fetch.cached(seasons)}
    with open(files["nflverse_players.csv"], newline="") as fh:
        players = nflverse.parse_players(csv.DictReader(fh))
    lines = []
    for s in seasons:
        with open(files[f"nflverse_stats_player_reg_{s}.csv"], newline="") as fh:
            lines += nflverse.parse_player_seasons(csv.DictReader(fh), s, "REG", skipped=[])
    return Resolver(players, lines)


def summary(rows):
    by = {}
    for r in rows:
        by.setdefault(r["award_type"], [0, 0])
        by[r["award_type"]][0] += 1
        by[r["award_type"]][1] += 0 if (r["gsis_id"] or r["coach_name"]) else 1
    return by


def _seasons(s):
    a, _, b = s.partition("-")
    return range(int(a), int(b or a) + 1)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m engines.nfl_history.awards",
                                 description="Build data/history/nfl_awards_1999_2025.csv from Wikipedia.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="fetch (cached) award pages and write the CSV")
    b.add_argument("--seasons", default=f"{FIRST_SEASON}-{LAST_SEASON}")
    b.add_argument("--refresh", action="store_true")
    b.add_argument("--out", default=AWARDS_CSV)
    b.add_argument("--no-resolve", action="store_true", help="leave gsis_id blank (no nflverse cache needed)")
    args = ap.parse_args(argv)
    rows = build(_seasons(args.seasons), args.refresh, args.out, not args.no_resolve)
    for award, (n, unresolved) in summary(rows).items():
        print(f"{award:22} {n:5} rows, {unresolved} without gsis_id")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
