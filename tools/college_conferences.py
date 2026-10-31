"""
Builds data/scouting/colleges.json's schools from Wikipedia's current football program lists, so every school in
Madden 27's college list (data/scouting/madden27_colleges.json) that plays football has its conference, nickname and
level (PROJECT_SPEC §2.18, §3.3c).

  python3 tools/college_conferences.py fetch <dir>             # download the six lists' wikitext into <dir>
  python3 tools/college_conferences.py build <dir> [--dry-run] # match, rebuild schools, print the report

Sources (football conferences, not a school's primary conference): List of NCAA Division I FBS / FCS (plus the FCS
list's "Transitioning from Division II" table) / Division II / Division III football programs, List of NAIA football
programs, U Sports football (the four conference tables). Levels: P4 (ACC, Big Ten, Big 12, SEC and Notre Dame), FBS,
FCS, D2, D3, NAIA, U Sports.

Each Madden name is matched to one program: exactly one program whose name (any of its spellings, letters only, with
University / College / The / Of dropped) equals the Madden name after expanding Madden's abbreviations (W. -> Western,
SE -> Southeastern / Southeast, N.C. -> North Carolina, UW -> Wisconsin, Tenn- -> UT ...), else MANUAL. NO_PROGRAM
lists Madden names with no current football program (closed schools, schools without football, junior colleges,
placeholders); they stay out of the list and their prospects get non-conference stories.

A school already in the list keeps its key and display name; its nickname, conference and level are rewritten from the
source. Keys are Madden's name upper-cased (the board's college), except where an existing key is Madden's name through
an alias (LA. TECH for L.A. Tech).
"""
import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COLLEGES = os.path.join(ROOT, "data", "scouting", "colleges.json")
MADDEN = os.path.join(ROOT, "data", "scouting", "madden27_colleges.json")
PAGES = {
    "FBS": "List_of_NCAA_Division_I_FBS_football_programs",
    "FCS": "List_of_NCAA_Division_I_FCS_football_programs",
    "D2": "List_of_NCAA_Division_II_football_programs",
    "D3": "List_of_NCAA_Division_III_football_programs",
    "NAIA": "List_of_NAIA_football_programs",
    "U Sports": "U_Sports_football",
}
P4 = {"ACC", "Big Ten", "Big 12", "SEC"}

# (division, the list's conference label) -> the conference name stored. Scouting pools schools by this string, so
# labels that repeat across divisions (D3's MAC and MIAA) get their full names.
CONFERENCES = {
    ("FBS", "CUSA"): "Conference USA",
    ("FCS", "CAA Football"): "CAA", ("FCS", "Ivy"): "Ivy League", ("FCS", "MVFC"): "Missouri Valley",
    ("FCS", "NEC"): "Northeast", ("FCS", "PFL"): "Pioneer League", ("FCS", "Patriot"): "Patriot League",
    ("FCS", "UAC"): "United Athletic", ("FCS", "Independent"): "FCS Independent",
    ("D2", "Independent"): "D2 Independent",
    ("D3", "MAC"): "Middle Atlantic", ("D3", "MIAA"): "Michigan Intercollegiate", ("D3", "MWC"): "Midwest Conference",
    ("D3", "Liberty"): "Liberty League", ("D3", "Independent"): "D3 Independent",
    ("NAIA", "Independent"): "NAIA Independent",
}

# Madden's abbreviations, expanded before matching (each Madden name is tried as-is and with every expansion)
EXPANSIONS = [(r"^W\. ", "Western "), (r"^C\. ", "Central "), (r"^S\. ", "Southern "), (r"^SE ", "Southeastern "),
              (r"^SE ", "Southeast "), (r"^SW ", "Southwestern "), (r"^NW ", "Northwest "), (r"^NW ", "Northwestern "),
              (r"^NE ", "Northeast "), (r"^N\.C\. ", "North Carolina "), (r"^UW[- ]", "Wisconsin–"),
              (r"^Wisc-", "Wisconsin–"), (r"^Tenn-", "UT "), (r"^Univ\.? (of )?", ""), (r"^Uni\. ", ""),
              (r"^U of ", ""), (r" University$", ""), (r"^St\. ", "Saint "), (r" State$", " State University")]

# Madden name -> a name of exactly one program in that division (where matching can't decide or spellings differ)
MANUAL = {
    "Augustana": ("D2", "Augustana (SD)"), "Benedictine College": ("NAIA", "Benedictine College"),
    "Bethel": ("D3", "Bethel"), "California": ("FBS", "California"), "California (PA)": ("D2", "California (PA)"),
    "Carroll College": ("NAIA", "Carroll College"), "Concordia": ("D3", "Concordia (Moorhead)"),
    "Cornell": ("FCS", "Cornell"), "Georgetown": ("FCS", "Georgetown"), "Illinois": ("FBS", "Illinois"),
    "Miami": ("FBS", "Miami (FL)"), "Monmouth": ("FCS", "Monmouth"), "Northwestern": ("FBS", "Northwestern"),
    "Ottawa": ("U Sports", "Ottawa"), "Texas": ("FBS", "Texas"), "Trinity": ("D3", "Trinity (CT)"),
    "Washington": ("FBS", "University of Washington"), "Wayne State": ("D2", "Wayne State (MI)"),
    "Westminster": ("D3", "Westminster (PA)"),
    "Beth Cookman": ("FCS", "Bethune–Cookman"), "Cal Poly SLO": ("FCS", "Cal Poly"), "FAU": ("FBS", "Florida Atlantic"),
    "L.A. Tech": ("FBS", "Louisiana Tech"), "Massachusetts": ("FBS", "UMass"), "Miami of Ohio": ("FBS", "Miami (OH)"),
    "N.C. A&T": ("FCS", "North Carolina A&T"), "North Arizona": ("FCS", "Northern Arizona"),
    "Tenn-Chattanooga": ("FCS", "Chattanooga"), "Tenn-Martin": ("FCS", "UT Martin"),
    "UL Lafayette": ("FBS", "Louisiana"), "UL Monroe": ("FBS", "ULM"), "USF": ("FBS", "South Florida"),
    "W. Kentucky": ("FBS", "Western Kentucky"), "W. Michigan": ("FBS", "Western Michigan"),
    "W. Carolina": ("FCS", "Western Carolina"), "W. Illinois": ("FCS", "Western Illinois"),
    "SE Louisiana": ("FCS", "Southeastern Louisiana"), "SE Missouri State": ("FCS", "Southeast Missouri State"),
    "Texas A&M-Commerce": ("FCS", "East Texas A&M"), "Houston Baptist": ("FCS", "Houston Christian"),
    "Mercyhurst University": ("FCS", "Mercyhurst University"), "West Georgia": ("FCS", "University of West Georgia"),
    "West Florida": ("FCS", "University of West Florida"), "Saginaw Valley": ("D2", "Saginaw Valley State"),
    "NW Missouri State": ("D2", "Northwest Missouri State"), "NW Oklahoma State": ("D2", "Northwestern Oklahoma State"),
    "SE Oklahoma State": ("D2", "Southeastern Oklahoma State"), "SW Oklahoma State": ("D2", "Southwestern Oklahoma State"),
    "W. New Mexico": ("D2", "Western New Mexico"), "W. Texas A&M": ("D2", "West Texas A&M"),
    "C. Connecticut State": ("FCS", "Central Connecticut"), "S. Connecticut State": ("D2", "Southern Connecticut"),
    "Mesa State": ("D2", "Colorado Mesa"), "Mines": ("D2", "Colorado School of Mines"),
    "Univ of Minnesota Duluth": ("D2", "Minnesota Duluth"), "Uni. Arkansas Monticello": ("D2", "Arkansas–Monticello"),
    "Winston Salem": ("D2", "Winston-Salem State"),
    "Wisc-Whitewater": ("D3", "Wisconsin–Whitewater"), "UW La Crosse": ("D3", "Wisconsin–La Crosse"),
    "UW Stevens Point": ("D3", "Wisconsin–Stevens Point"), "UW-Stout": ("D3", "Wisconsin–Stout"),
    "St. John's": ("D3", "Saint John's (MN)"), "St. Norbert": ("D3", "St. Norbert"),
    "Rensselaer Poly": ("D3", "RPI"), "Ramapo": None, "Cumberlands": ("NAIA", "University of the Cumberlands"),
    "Pikeville": ("NAIA", "University of Pikeville"), "Florida Tech": None, "Malone": None,
    "Concordia Univ.-St. Paul": ("D2", "Concordia University, Saint Paul"), "St. Cloud State": None,
    "Clinch Valley": ("D2", "UVA Wise"), "Limestone": None, "Western Washington": None,
    "Humboldt State": None, "Sonoma State": None, "Walla Walla": None, "Nebraska-Omaha": None,
    "Texas-Permian Basin": ("D2", "UT Permian Basin"), "St. Augustine": None, "Stillman": None,
    "SW Assemblies of God": None, "Virginia-Lynchburg": None, "St. Paul's": None,
    "Central State (OH)": ("D2", "Central State"), "Wheaton": ("D3", "Wheaton (IL)"), "Idaho": ("FCS", "Idaho"),
    "St. Francis": ("D3", "Saint Francis"), "St. Mary's": ("U Sports", "Saint Mary's"),
    "Simon Fraser": None,
    "Arizona Christian": ("NAIA", "Arizona Christian University"), "Arkansas at Pine Bluff": ("FCS", "Arkansas–Pine Bluff"),
    "Baker": ("NAIA", "Baker University"), "Bentley College": ("D2", "Bentley"), "Connecticut": ("FBS", "UConn"),
    "Culver-Stockton": ("NAIA", "Culver–Stockton College"), "Eastern Oregon": ("NAIA", "Eastern Oregon University"),
    "Faulkner": ("NAIA", "Faulkner University"), "Franklin College": ("D3", "Franklin"),
    "Lindsey Wilson College": ("NAIA", "Lindsey Wilson University"),
    "Louisiana Christian": ("NAIA", "Louisiana Christian University"), "Marian": ("NAIA", "Marian University"),
    "Morningside College": ("NAIA", "Morningside University"), "Southern Oregon": ("NAIA", "Southern Oregon University"),
    "Tusculum College": ("D2", "Tusculum"), "William Penn": ("NAIA", "William Penn University"),
    "Alberta": ("U Sports", "Alberta"), "Calgary": ("U Sports", "Calgary"), "Laval": ("U Sports", "Laval"),
    "Manitoba": ("U Sports", "Manitoba"), "McGill University": ("U Sports", "McGill"),
    "Montreal University": ("U Sports", "Montreal"), "Queen's University": ("U Sports", "Queen's"),
    "Regina": ("U Sports", "Regina"), "U of Saskatchewan": ("U Sports", "Saskatchewan"),
    "Univ. British Columbia": ("U Sports", "UBC"), "Western Ontario": ("U Sports", "Western"),
    "Western University": ("U Sports", "Western"), "York University": ("U Sports", "York"),
}

# No current football program in the source lists (MANUAL entries set to None are checked the same way)
NO_PROGRAM = {
    "N/A", "No College", "None",
    "Cal State Bakersfield", "Cal State Northridge", "Canisius", "Fairfield", "Hofstra", "Iona", "La Salle",
    "Northeastern", "Seattle", "Siena", "St. Peters", "UC Irvine", "UW-Milwaukee", "Virginia Commonwealth",
    "Jacksonville University", "Knoxville College", "Lambuth",
    "Morris Brown", "Wesley College", "Mt. S. Antonio College", "NE Mississippi Community", "Palomar College",
    "Scottsbluff JC", "SW Mississippi State", "Pretoria", "Mississippi College", "Notre Dame College",
}


# --------------------------------------------------------------------------------------------- wikitext ----
def _strip_nested(s, op, cl):
    out, depth, i = [], 0, 0
    while i < len(s):
        if s.startswith(op, i):
            depth, i = depth + 1, i + len(op)
            continue
        if depth and s.startswith(cl, i):
            depth, i = depth - 1, i + len(cl)
            continue
        if not depth:
            out.append(s[i])
        i += 1
    return "".join(out)


def clean(s):
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    s = re.sub(r"<ref[^>]*/>", "", s)
    s = re.sub(r"<ref[^>]*>.*?</ref>", "", s, flags=re.S)
    s = re.sub(r"\{\{sort\|[^|}]*\|", "{{x|", s)          # {{sort|key|[[Link]]}} keeps the link
    s = re.sub(r"\{\{x\|(.*?)\}\}", r"\1", s)
    s = _strip_nested(s, "{{", "}}")
    s = re.sub(r"<br\s*/?>", " ", s)
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"'''?", "", s)


def links(s):
    return [(t.strip(), (d or t).strip()) for t, d in re.findall(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", s)]


def text(s):
    s = re.sub(r"\[\[(?:[^\]|]+\|)?([^\]]+)\]\]", r"\1", s)
    s = re.sub(r"^\s*(style|align|colspan|rowspan|class)=[^|]*\|", "", s)
    return re.sub(r"\s+", " ", s).strip().lstrip("|").strip()


def tables(src):
    """[(heading, [lines])] for every wikitable, with the section heading above it."""
    out, cur, heading = [], None, None
    for line in src.split("\n"):
        h = re.match(r"^=+\s*(.*?)\s*=+\s*$", line)
        if h and cur is None:
            heading = h.group(1)
        if line.startswith("{|"):
            cur = []
            continue
        if line.startswith("|}"):
            if cur is not None:
                out.append((heading, cur))
            cur = None
            continue
        if cur is not None:
            cur.append(line)
    return out


def rows(lines):
    rs, cur = [], []
    for line in lines:
        if line.startswith("|-") or line.startswith("|+"):
            if cur:
                rs.append(cur)
            cur = []
        elif line[:1] in ("!", "|"):
            kind = "h" if line[0] == "!" else "d"
            cur += [(kind, c) for c in line[1:].split("!!" if kind == "h" else "||")]
        elif cur:
            k, c = cur[-1]
            cur[-1] = (k, c + " " + line)
    if cur:
        rs.append(cur)
    return rs


def table_programs(lines, division, name_cols, nick_col, conf_col=None, conference=None):
    rs = rows(lines)
    head = [text(c).lower() for k, c in rs[0] if k == "h"]
    out = []
    for r in rs[1:]:
        cells = [c for k, c in r if k == "d"]
        if len(cells) < 3:
            continue

        def col(name):
            i = next((i for i, h in enumerate(head) if h.startswith(name)), None)
            return cells[i] if i is not None and i < len(cells) else ""
        names = set()
        for c in name_cols:
            for tgt, disp in links(col(c)):
                names.update((tgt, disp))
            if text(col(c)):
                names.add(text(col(c)))
        nick = text(col(nick_col))
        for n in list(names):                             # "Abilene Christian Wildcats football" -> also bare name
            bare = re.sub(r"\s+football$", "", n)
            if nick and bare.endswith(" " + nick):
                names.add(bare[: -len(nick) - 1])
        label = conference if conference else text(col(conf_col))
        out.append({"division": division, "names": sorted(n for n in names if n), "nickname": nick,
                    "label": label})
    return out


def _bare(name):
    """'University of West Florida' / 'Mercyhurst University' / 'Albion College' -> the school's own name."""
    return re.sub(r"\s+(University|College)$", "", re.sub(r"^University of (the )?", "", name))


def programs(wiki_dir):
    """Every program in the six lists: {division, names, nickname, label, conference, level}."""
    def src(div):
        with open(os.path.join(wiki_dir, PAGES[div] + ".wiki")) as fh:
            return tables(clean(fh.read()))
    out = []
    fbs = src("FBS")
    out += table_programs(fbs[0][1], "FBS", ["school"], "nickname", "current")
    fcs = src("FCS")
    out += table_programs(fcs[0][1], "FCS", ["team", "school"], "name", "conference")
    trans = next(t for h, t in fcs if h and "Transitioning from Division II" in h)
    out += table_programs(trans, "FCS", ["team", "school"], "team", "conference")
    out += table_programs(src("D2")[0][1], "D2", ["school", "common"], "nickname", "conference")
    d3 = [t for h, t in src("D3") if any(l.startswith("!Team") for l in t)]
    out += table_programs(d3[0], "D3", ["team"], "nickname", "current")
    out += table_programs(src("NAIA")[0][1], "NAIA", ["institution"], "nickname", "conference")
    for heading, t in src("U Sports"):
        if heading in ("Atlantic University Sport", "Canada West Universities Athletic Association",
                       "Ontario University Athletics", "Réseau du sport étudiant du Québec"):
            short = {"Atlantic University Sport": "AUS", "Canada West Universities Athletic Association": "Canada West",
                     "Ontario University Athletics": "OUA", "Réseau du sport étudiant du Québec": "RSEQ"}[heading]
            out += table_programs(t, "U Sports", ["institution", "team"], "team", conference=short)
    for p in out:
        prefixes = set(p["names"]) | {_bare(n) for n in p["names"]}
        for n in sorted(prefixes, key=len, reverse=True):     # "West Georgia Wolves" (a team column) -> "Wolves"
            if p["nickname"].startswith(n + " "):
                p["nickname"] = p["nickname"][len(n) + 1:]
                break
        if p["division"] == "FCS" and p["label"] == "":
            p["label"] = "Independent"
        p["label"] = re.sub(r"\s*\(.*$", "", p["label"]).strip()
        p["conference"] = CONFERENCES.get((p["division"], p["label"]), p["label"])
        if p["division"] == "FBS":
            p["level"] = "P4" if p["conference"] in P4 or "Notre Dame" in p["names"] else "FBS"
        else:
            p["level"] = p["division"]
    return out


# --------------------------------------------------------------------------------------------- matching ----
def norm(s):
    s = s.upper().replace("–", "-").replace("—", "-")
    s = re.sub(r"\b(THE|FOOTBALL)\b", " ", s)
    return re.sub(r"[^A-Z0-9&()]", "", s)


def variants(name):
    out = {name}
    for pat, rep in EXPANSIONS:
        for v in list(out):
            out.add(re.sub(pat, rep, v))
    return out


def match(madden_names, progs):
    """({madden name: program}, unmatched [names], ambiguous {name: [program names]})."""
    index = {}
    for i, p in enumerate(progs):
        for n in p["names"]:
            index.setdefault(norm(n), set()).add(i)
    by_div = {}
    for i, p in enumerate(progs):
        for n in p["names"]:
            by_div.setdefault((p["division"], norm(n)), set()).add(i)
    found, unmatched, ambiguous = {}, [], {}
    for name in madden_names:
        if name in NO_PROGRAM or (name in MANUAL and MANUAL[name] is None):
            continue
        if name in MANUAL:
            div, target = MANUAL[name]
            hits = by_div.get((div, norm(target)), set())
            if len(hits) != 1:
                unmatched.append(f"{name} (MANUAL {div} {target!r} matches {len(hits)} programs)")
                continue
            found[name] = progs[next(iter(hits))]
            continue
        hits = set()
        for v in variants(name):
            hits |= index.get(norm(v), set())
        if len(hits) == 1:
            found[name] = progs[next(iter(hits))]
        elif hits:
            ambiguous[name] = [f"{progs[i]['names'][0]} ({progs[i]['division']})" for i in hits]
        else:
            unmatched.append(name)
    return found, unmatched, ambiguous


def common_name(p, madden_name):
    """The program's everyday name for writer text: the shortest name that isn't a link to its football page."""
    names = [n for n in set(p["names"]) | {_bare(x) for x in p["names"]} if not n.endswith(" football")
             and "University" not in n and "College" not in n and n != p["nickname"] and not n.endswith(" " + p["nickname"])]
    return min(names, key=len) if names else madden_name


def build(wiki_dir, dry_run=False):
    with open(MADDEN) as fh:
        mdoc = json.load(fh)
    madden_names = sorted({c["name"] for c in mdoc["colleges"]} | set(mdoc.get("overrides", {}).values()))
    progs = programs(wiki_dir)
    found, unmatched, ambiguous = match(madden_names, progs)
    with open(COLLEGES) as fh:
        doc = json.load(fh)
    schools, aliases = doc["schools"], doc["aliases"]
    changed, added = [], []
    for name, p in sorted(found.items()):
        key = name.upper()
        key = aliases.get(key, key)
        old = schools.get(key)
        entry = {"display": old["display"] if old else common_name(p, name), "nickname": p["nickname"],
                 "conference": p["conference"], "level": p["level"]}
        if old is None:
            added.append(key)
        elif (old["conference"], old["level"], old["nickname"]) != (entry["conference"], entry["level"], entry["nickname"]):
            changed.append((key, f"{old['conference']}/{old['level']}/{old['nickname']}",
                            f"{entry['conference']}/{entry['level']}/{entry['nickname']}"))
        schools[key] = entry
    madden_keys = {aliases.get(n.upper(), n.upper()) for n in found}
    not_madden = sorted(k for k in schools if k not in madden_keys)
    doc["schools"] = dict(sorted(schools.items()))
    doc["_note"] = ("Draft-class colleges -> conference, nickname and level (docs/done/team_management/"
                    "04_SCOUTING_AND_DRAFT_PLAN.md §2.6). Every school of Madden 27's college list that plays football "
                    "(tools/college_conferences.py, from Wikipedia's football program lists; football conferences as "
                    "of the 2026 season). Invented schedules and title games stay inside the school's conference. "
                    "aliases maps Draft Board OCR spellings to a school.")
    rep = {"schools": len(schools), "added": len(added), "changed": changed, "unmatched": unmatched,
           "ambiguous": ambiguous, "not_in_madden_list": not_madden,
           "no_program": sorted(NO_PROGRAM | {n for n, v in MANUAL.items() if v is None})}
    if not dry_run and not unmatched and not ambiguous:
        with open(COLLEGES, "w") as fh:
            json.dump(doc, fh, indent=1)
            fh.write("\n")
    return rep, doc


def fetch(dest):
    os.makedirs(dest, exist_ok=True)
    for page in PAGES.values():
        url = f"https://en.wikipedia.org/w/index.php?title={urllib.parse.quote(page)}&action=raw"
        req = urllib.request.Request(url, headers={"User-Agent": "ProjectMu-college-conferences/1.0"})
        with urllib.request.urlopen(req, timeout=60) as r, open(os.path.join(dest, page + ".wiki"), "wb") as fh:
            fh.write(r.read())
        print(f"fetched {page}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("dir")
    b = sub.add_parser("build")
    b.add_argument("dir")
    b.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "fetch":
        fetch(a.dir)
        return 0
    rep, _ = build(a.dir, a.dry_run)
    print(json.dumps(rep, indent=1, ensure_ascii=False))
    return 1 if rep["unmatched"] or rep["ambiguous"] else 0


if __name__ == "__main__":
    sys.exit(main())
