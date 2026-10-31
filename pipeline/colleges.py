"""
Madden 27's college list: Player.College reference -> college name (PROJECT_SPEC §3.3c).

Player.College is a reference into the game's static college tables (table 16384 holds all but 11 of them), which live in
the game files, not the save, so a snapshot (pipeline/save_reader/pc_save_reader.mjs, `college` = {table, row}) only
carries the reference. The names come from data/scouting/madden27_colleges.json: Pipeline 0.5.0's college lookup (488
colleges in Madden's own display spelling, MIT, credited in the file), imported unchanged. `overrides` there
({"table:row": name}, set with `set`) always win.

Board colleges are written upper-cased, like the PS5 Draft Board scan (`Ole Miss` -> OLE MISS), so the scouting
college list (data/scouting/colleges.json) finds their conference.

`check` cross-checks the list against saves: each real NFL player whose name matches exactly one nflverse player
(players.csv, last season 2022 or later) votes for that player's final college (the first school nflverse lists), and
every reference with 2+ votes and 60%+ agreeing must name the same school (spelling aside; MADDEN_SPELLING folds
nflverse names into Madden's). CAREER-WEEK1 + CAREER-GIANTS-AUTOSAVE: 229 references, all agree.

  python3 -m pipeline.colleges check <nflverse players.csv> <snapshot.json> [<snapshot.json> ...]
  python3 -m pipeline.colleges set <table:row> "<Name>"        # a correction; "" removes it
  python3 -m pipeline.colleges show [--snapshot snapshot.json] # the list, and a snapshot's prospects without a college
"""
import argparse
import collections
import csv
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIST_PATH = os.path.join(ROOT, "data", "scouting", "madden27_colleges.json")
LAST_SEASON = 2022                  # nflverse players still active in the saves' era
MIN_VOTES, MIN_SHARE = 2, 0.6

# nflverse spellings -> Madden's (only where normalising the letters isn't enough)
MADDEN_SPELLING = {
    "MISSISSIPPI": "OLE MISS", "BOWLING GREEN": "BOWLING GREEN STATE", "FLORIDA ATLANTIC": "FAU",
    "FLORIDA INTERNATIONAL": "FIU", "UNIVERSITY OF SOUTH FLORIDA": "USF", "SOUTH FLORIDA": "USF",
    "LOUISIANA TECH": "L.A. TECH", "WESTERN KENTUCKY": "W. KENTUCKY", "WESTERN MICHIGAN": "W. MICHIGAN",
    "LOUISIANA-LAFAYETTE": "UL LAFAYETTE", "LOUISIANA": "UL LAFAYETTE", "LOUISIANA-MONROE": "UL MONROE",
    "MIAMI (OHIO)": "MIAMI OF OHIO", "MIAMI (FL)": "MIAMI", "SOUTHERN MISSISSIPPI": "SOUTHERN MISS",
    "SOUTHERN UTAH STATE": "SOUTHERN UTAH", "TEXAS-EL PASO": "UTEP", "TEXAS-SAN ANTONIO": "UTSA",
    "UMASS AMHERST": "MASSACHUSETTS", "CENTRAL FLORIDA": "UCF", "ALABAMA-BIRMINGHAM": "UAB",
    "TEXAS CHRISTIAN": "TCU", "SOUTHERN CALIFORNIA": "USC", "BETHUNE-COOKMAN": "BETH COOKMAN",
    "NORTH CAROLINA STATE": "N.C. STATE", "MIDDLE TENNESSEE": "MIDDLE TENNESSEE STATE",
    "TEXAS STATE-SAN MARCOS": "TEXAS STATE", "STEPHEN F. AUSTIN STATE": "STEPHEN F. AUSTIN",
    "TENNESSEE-CHATTANOOGA": "TENN-CHATTANOOGA", "COLORADO STATE (PUEBLO)": "CSU-PUEBLO",
    "PITTSBURG STATE (KS)": "PITTSBURG STATE", "MANITOBA (CANADA)": "MANITOBA", "INDIANA PA, UNIVERSITY OF": "IUP",
    "CAL POLY (SAN LUIS OBISPO)": "CAL POLY SLO", "NORTHERN ARIZONA": "NORTH ARIZONA", "NORTH CAROLINA A&T": "N.C. A&T",
    "SOUTHEASTERN LOUISIANA": "SE LOUISIANA", "SOUTHEAST MISSOURI STATE": "SE MISSOURI STATE",
    "WESTERN CAROLINA": "W. CAROLINA", "WESTERN ILLINOIS": "W. ILLINOIS", "WISCONSIN-WHITEWATER": "WISC-WHITEWATER",
    "EAST TEXAS A&M": "TEXAS A&M-COMMERCE", "WESTERN STATE": "WESTERN COLORADO", "MISSOURI-ROLLA": "MISSOURI S&T",
    "UNIVERSITY OF BRITISH COLUMBIA": "UNIV. BRITISH COLUMBIA",
}


def madden_name(nflverse_name):
    """An nflverse college name in Madden's spelling (upper case): MADDEN_SPELLING, else with 'University of' and a
    trailing 'University' dropped ('Delta State University' -> DELTA STATE)."""
    n = re.sub(r"\s+", " ", (nflverse_name or "").strip()).upper().rstrip(".")
    if n in MADDEN_SPELLING:
        return MADDEN_SPELLING[n]
    n = re.sub(r"^UNIVERSITY OF ", "", n)
    n = re.sub(r" (UNIVERSITY|UNIV)$", "", n)
    return MADDEN_SPELLING.get(n, n)


def same_school(a, b):
    """Two spellings of one school: equal as letters and digits once a parenthetical and trailing UNIVERSITY / COLLEGE
    / STATE words go (Elon University = Elon, Fort Valley State College = Fort Valley State, St. John's (MN) =
    St. John's)."""
    def key(s):
        s = re.sub(r"\(.*?\)", "", s.upper()).strip()
        while True:
            t = re.sub(r"\s+(UNIVERSITY|COLLEGE|STATE)$", "", s)
            if t == s:
                return re.sub(r"[^A-Z0-9&]", "", s)
            s = t
    return key(a) == key(b)


# ------------------------------------------------------------------------------------------------------ list ----
def load_list(path=LIST_PATH):
    with open(path) as fh:
        return json.load(fh)


_LOOKUPS = {}


def lookup(path=LIST_PATH):
    """{(table, row): Madden name} with the overrides applied (cached per file)."""
    if path not in _LOOKUPS:
        doc = load_list(path)
        out = {(c["table"], c["row"]): c["name"] for c in doc["colleges"]}
        for ref, name in doc.get("overrides", {}).items():
            t, r = ref.split(":")
            out[(int(t), int(r))] = name
        _LOOKUPS[path] = out
    return _LOOKUPS[path]


def college_name(ref, path=LIST_PATH):
    """A snapshot `college` reference -> the board college (Madden's name, upper case); None when unset or unknown."""
    if not ref:
        return None
    name = lookup(path).get((ref.get("table"), ref.get("row")))
    return name.upper() if name else None


# ----------------------------------------------------------------------------------------------------- check ----
def _name_key(first, last):
    return re.sub(r"[^A-Z]", "", f"{first}{last}".upper())


def nflverse_colleges(path):
    """{name key: {final college in Madden's spelling}} for nflverse players active in LAST_SEASON or later."""
    out = collections.defaultdict(set)
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            if not r.get("college_name") or int(r.get("last_season") or 0) < LAST_SEASON:
                continue
            college = madden_name(r["college_name"].split(";")[0])
            for first in {r["first_name"], r.get("football_name") or r["first_name"]}:
                out[_name_key(first, r["last_name"])].add(college)
    return out


def votes(snapshot, nfl):
    """{(table, row): Counter(name)} from the save's non-prospect players with exactly one nflverse match."""
    out = collections.defaultdict(collections.Counter)
    for p in snapshot["players"]:
        ref = p.get("college")
        if not ref or p["status"] == "Draft":
            continue
        hits = nfl.get(_name_key(p["first_name"], p["last_name"]), set())
        if len(hits) == 1:
            out[(ref["table"], ref["row"])][next(iter(hits))] += 1
    return out


def check(ref_votes, names):
    """Compare the voted references with the list (pure). Only references with MIN_VOTES+ votes and MIN_SHARE
    agreeing are checked. Returns {checked, agree, differ: [(ref, voted, listed)], missing: [(ref, voted)]}."""
    rep = {"checked": 0, "agree": 0, "differ": [], "missing": []}
    for ref, cnt in sorted(ref_votes.items()):
        (voted, n), total = cnt.most_common(1)[0], sum(cnt.values())
        if n < MIN_VOTES or n / total < MIN_SHARE:
            continue
        rep["checked"] += 1
        listed = names.get(ref)
        if listed is None:
            rep["missing"].append((ref, voted))
        elif same_school(voted, listed):
            rep["agree"] += 1
        else:
            rep["differ"].append((ref, voted, listed))
    return rep


def unnamed_prospects(snapshot, path=LIST_PATH):
    return [p for p in snapshot["players"] if p["status"] == "Draft" and college_name(p.get("college"), path) is None]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("players_csv")
    c.add_argument("snapshots", nargs="+")
    s = sub.add_parser("set")
    s.add_argument("ref", help="table:row, e.g. 16384:2028")
    s.add_argument("name")
    sh = sub.add_parser("show")
    sh.add_argument("--snapshot")
    a = ap.parse_args(argv)
    if a.cmd == "check":
        nfl = nflverse_colleges(a.players_csv)
        pooled = collections.defaultdict(collections.Counter)
        for path in a.snapshots:
            with open(path) as fh:
                snap = json.load(fh)
            for ref, cnt in votes(snap, nfl).items():
                pooled[ref].update(cnt)
            print(f"{os.path.basename(os.path.dirname(os.path.abspath(path)))}: "
                  f"{len(unnamed_prospects(snap))} prospects without a college")
        rep = check(pooled, lookup())
        print(f"{rep['checked']} references checked: {rep['agree']} agree, {len(rep['differ'])} differ, "
              f"{len(rep['missing'])} not in the list")
        for ref, voted, listed in rep["differ"]:
            print(f"  differ {ref[0]}:{ref[1]}: players say {voted}, the list says {listed}")
        for ref, voted in rep["missing"]:
            print(f"  missing {ref[0]}:{ref[1]}: players say {voted}")
        return 1 if rep["differ"] or rep["missing"] else 0
    if a.cmd == "set":
        t, r = (int(x) for x in a.ref.split(":"))
        doc = load_list()
        ov = doc.setdefault("overrides", {})
        if a.name:
            ov[f"{t}:{r}"] = a.name.strip()
        else:
            ov.pop(f"{t}:{r}", None)
        with open(LIST_PATH, "w") as fh:
            json.dump(doc, fh, indent=1)
            fh.write("\n")
        print(f"{t}:{r}: {ov.get(f'{t}:{r}', 'override removed')}")
        return 0
    for (t, r), name in sorted(lookup().items()):
        print(f"{t}:{r:<7} {name}")
    if a.snapshot:
        with open(a.snapshot) as fh:
            missing = unnamed_prospects(json.load(fh))
        print("prospects without a college:",
              [(f"{p['first_name']} {p['last_name']}", p.get("college")) for p in missing] or "none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
