"""
Madden 27's college list: Player.College row -> college name (docs: PROJECT_SPEC §3.3c).

Player.College is a reference into the game's static college list (table 16384), which lives in the game files, not
the save, so a snapshot (pipeline/save_reader/pc_save_reader.mjs, `college` = {table, row}) only carries the row. The
names are fitted from the saves' real NFL players (several snapshots pool their votes; the list is the game's, so
every save agrees: CAREER-WEEK1 and CAREER-GIANTS-AUTOSAVE give the same name on all 184 rows both map): each player whose name matches exactly one nflverse player
(players.csv, last season 2022 or later) votes for that player's final college (the first school nflverse lists), and a
row takes the winning name when

  * 2+ votes and 60%+ of them agree, or
  * a single vote whose name sorts between the nearest confirmed rows on either side (the list is alphabetical by
    Madden's own spelling, with a few later additions at high row numbers, which need 2+ votes), or
  * no vote, but its two neighbours (row - 1, row + 1) are mapped and exactly one school of the scouting college list
    sorts strictly between their names (1745 between AIR FORCE and ALABAMA is AKRON).

Names are written in Madden's own Draft Board spelling where the scouting college list (data/scouting/colleges.json,
read off the PS5 Draft Board) has the school ("Mississippi" -> OLE MISS, "Louisiana Tech" -> LA. TECH), else
upper-cased. The map is data/scouting/madden27_college_rows.json; `overrides` there (JR's in-game corrections, set with
`set`) always win and survive a re-fit.

  python3 -m pipeline.colleges fit <nflverse players.csv> <snapshot.json> [<snapshot.json> ...] [--out PATH]
  python3 -m pipeline.colleges set <row> "<NAME>"          # an in-game correction; "" removes it
  python3 -m pipeline.colleges show [--snapshot snapshot.json]   # every row, and the prospects' unmapped rows
"""
import argparse
import collections
import csv
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP_PATH = os.path.join(ROOT, "data", "scouting", "madden27_college_rows.json")
COLLEGE_TABLE = 16384
LAST_SEASON = 2022                  # nflverse players still active in the save's era
MIN_VOTES, MIN_SHARE = 2, 0.6
ALPHA_ROWS = 2200                   # rows below this are the alphabetical base list

# nflverse spellings -> Madden's Draft Board spelling (the scouting list's keys)
MADDEN_SPELLING = {
    "MISSISSIPPI": "OLE MISS", "BOWLING GREEN": "BOWLING GREEN STATE", "FLORIDA ATLANTIC": "FAU",
    "FLORIDA INTERNATIONAL": "FIU", "UNIVERSITY OF SOUTH FLORIDA": "USF", "SOUTH FLORIDA": "USF",
    "LOUISIANA TECH": "LA. TECH", "WESTERN KENTUCKY": "W. KENTUCKY", "WESTERN MICHIGAN": "W. MICHIGAN",
    "LOUISIANA-LAFAYETTE": "UL LAFAYETTE", "LOUISIANA": "UL LAFAYETTE", "LOUISIANA-MONROE": "UL MONROE",
    "MIAMI (OHIO)": "MIAMI OF OHIO", "MIAMI (FL)": "MIAMI", "SOUTHERN MISSISSIPPI": "SOUTHERN MISS",
    "SOUTHERN UTAH STATE": "SOUTHERN UTAH", "TEXAS-EL PASO": "UTEP", "TEXAS-SAN ANTONIO": "UTSA",
    "UMASS AMHERST": "MASSACHUSETTS", "CENTRAL FLORIDA": "UCF", "ALABAMA-BIRMINGHAM": "UAB",
    "TEXAS CHRISTIAN": "TCU", "SOUTHERN CALIFORNIA": "USC", "BETHUNE-COOKMAN": "BETH COOKMAN",
    "GRAND VALLEY": "GRAND VALLEY STATE", "NORTH CAROLINA STATE": "N.C. STATE",
    "MIDDLE TENNESSEE": "MIDDLE TENNESSEE STATE", "TEXAS STATE-SAN MARCOS": "TEXAS STATE",
    "NW MISSOURI STATE": "NORTHWEST MISSOURI STATE", "STEPHEN F. AUSTIN STATE": "STEPHEN F. AUSTIN",
    "TENNESSEE-CHATTANOOGA": "CHATTANOOGA", "COLORADO STATE (PUEBLO)": "COLORADO STATE-PUEBLO",
    "PITTSBURG STATE (KS)": "PITTSBURG STATE", "MANITOBA (CANADA)": "MANITOBA", "INDIANA PA": "INDIANA (PA)",
}


def madden_name(nflverse_name):
    """An nflverse college name in Madden's Draft Board spelling (upper case): 'University of' and a trailing
    'University' dropped ('Delta State University' -> DELTA STATE), then MADDEN_SPELLING."""
    n = re.sub(r"\s+", " ", (nflverse_name or "").strip()).upper().rstrip(".")
    n = re.sub(r"^UNIVERSITY OF ", "", n)
    n = re.sub(r",\s*UNIVERSITY OF$", "", n)
    n = re.sub(r" (UNIVERSITY|UNIV)$", "", n)
    return MADDEN_SPELLING.get(n, n)


def _sort_key(name):
    return re.sub(r"[^A-Z0-9]", "", name.upper())


def _name_key(first, last):
    return re.sub(r"[^A-Z]", "", f"{first}{last}".upper())


def nflverse_colleges(path):
    """{name key: {final college}} for nflverse players active in LAST_SEASON or later (first and football names)."""
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
    """{row: Counter(name)} from the save's non-prospect players with exactly one nflverse match."""
    out = collections.defaultdict(collections.Counter)
    for p in snapshot["players"]:
        ref = p.get("college")
        if not ref or ref["table"] != COLLEGE_TABLE or p["status"] == "Draft":
            continue
        hits = nfl.get(_name_key(p["first_name"], p["last_name"]), set())
        if len(hits) == 1:
            out[ref["row"]][next(iter(hits))] += 1
    return out


def fit_rows(row_votes, wanted=(), board_schools=()):
    """{row: {name, votes, of, rule}} from the votes (pure; the rules in the module docstring). wanted: rows to try
    the neighbour rule on (the prospects' rows); board_schools: the scouting list's school names."""
    rows = {}
    for row, cnt in row_votes.items():
        (name, n), total = cnt.most_common(1)[0], sum(cnt.values())
        if n >= MIN_VOTES and n / total >= MIN_SHARE:
            rows[row] = {"name": name, "votes": n, "of": total, "rule": "majority"}
    confirmed = sorted(r for r in rows if r < ALPHA_ROWS)
    for row, cnt in row_votes.items():
        if row in rows or row >= ALPHA_ROWS or sum(cnt.values()) != 1:
            continue
        name = next(iter(cnt))
        below = [r for r in confirmed if r < row]
        above = [r for r in confirmed if r > row]
        lo = _sort_key(rows[below[-1]]["name"]) if below else ""
        hi = _sort_key(rows[above[0]]["name"]) if above else "~"
        if lo < _sort_key(name) < hi:
            rows[row] = {"name": name, "votes": 1, "of": 1, "rule": "single vote in alphabetical order"}
    for row in sorted(set(wanted)):
        if row in rows or row - 1 not in rows or row + 1 not in rows:
            continue
        lo, hi = _sort_key(rows[row - 1]["name"]), _sort_key(rows[row + 1]["name"])
        between = [b for b in board_schools if lo < _sort_key(b) < hi]
        if len(between) == 1:
            rows[row] = {"name": between[0], "votes": 0, "of": 0, "rule": "only board school between its neighbours"}
    return rows


def board_schools():
    """The scouting college list's schools (Madden's Draft Board spellings)."""
    with open(os.path.join(ROOT, "data", "scouting", "colleges.json")) as fh:
        return sorted(json.load(fh)["schools"])


def load_map(path=MAP_PATH):
    if not os.path.exists(path):
        return {"rows": {}, "overrides": {}}
    with open(path) as fh:
        return json.load(fh)


_LOOKUPS = {}


def lookup(path=MAP_PATH):
    """{row: name} with the overrides applied (cached per map file)."""
    if path not in _LOOKUPS:
        m = load_map(path)
        rows = {int(r): v["name"] for r, v in m.get("rows", {}).items()}
        rows.update({int(r): n for r, n in m.get("overrides", {}).items()})
        _LOOKUPS[path] = rows
    return _LOOKUPS[path]


def college_name(ref, path=MAP_PATH):
    """A snapshot `college` reference -> its name (override first), None when unset or not mapped."""
    if not ref or ref.get("table") != COLLEGE_TABLE:
        return None
    return lookup(path).get(ref["row"])


def write_map(rows, overrides, path=MAP_PATH, source=None):
    doc = {"_note": "Madden 27 Player.College rows (table 16384) -> college, fitted by pipeline/colleges.py from a "
                    "save's real players and nflverse colleges; overrides are in-game corrections and always win.",
           "table": COLLEGE_TABLE, "source": source,
           "rows": {str(r): rows[r] for r in sorted(rows)},
           "overrides": {str(r): overrides[r] for r in sorted(overrides, key=int)}}
    with open(path, "w") as fh:
        json.dump(doc, fh, indent=1)
        fh.write("\n")


def prospect_rows(snapshot):
    return collections.Counter(p["college"]["row"] for p in snapshot["players"]
                               if p["status"] == "Draft" and p.get("college") and p["college"]["table"] == COLLEGE_TABLE)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fit")
    f.add_argument("players_csv")
    f.add_argument("snapshots", nargs="+")
    f.add_argument("--out", default=MAP_PATH)
    s = sub.add_parser("set")
    s.add_argument("row", type=int)
    s.add_argument("name")
    sh = sub.add_parser("show")
    sh.add_argument("--snapshot")
    a = ap.parse_args(argv)
    if a.cmd == "fit":
        nfl = nflverse_colleges(a.players_csv)
        pooled, pr, sources = collections.defaultdict(collections.Counter), collections.Counter(), []
        for path in a.snapshots:
            with open(path) as fh:
                snap = json.load(fh)
            for row, cnt in votes(snap, nfl).items():
                pooled[row].update(cnt)
            pr.update(prospect_rows(snap))
            sources.append(os.path.basename(snap["meta"].get("source") or path))
        rows = fit_rows(pooled, pr, board_schools())
        old = load_map(a.out)
        write_map(rows, old.get("overrides", {}), a.out, source=", ".join(sources))
        known = set(rows) | {int(r) for r in old.get("overrides", {})}
        print(f"{len(rows)} rows mapped ({sum(1 for r in rows.values() if r['votes'] == 1)} on a single vote); "
              f"prospects with a college: {sum(n for r, n in pr.items() if r in known)} of {sum(pr.values())}")
        print("prospects' unmapped rows:", sorted((r, n) for r, n in pr.items() if r not in known) or "none")
    elif a.cmd == "set":
        m = load_map()
        ov = m.get("overrides", {})
        if a.name:
            ov[str(a.row)] = a.name.strip().upper()
        else:
            ov.pop(str(a.row), None)
        write_map({int(r): v for r, v in m.get("rows", {}).items()}, ov, source=m.get("source"))
        print(f"row {a.row}: {ov.get(str(a.row), 'override removed')}")
    else:
        m = load_map()
        for r, v in sorted(m.get("rows", {}).items(), key=lambda kv: int(kv[0])):
            print(f"{r:>7}  {v['name']:<32} {v['votes']}/{v['of']} {v['rule']}")
        for r, n in m.get("overrides", {}).items():
            print(f"{r:>7}  {n:<32} override")
        if a.snapshot:
            with open(a.snapshot) as fh:
                pr = prospect_rows(json.load(fh))
            known = {int(r) for r in m.get("rows", {})} | {int(r) for r in m.get("overrides", {})}
            print("prospects' unmapped rows:", sorted((r, n) for r, n in pr.items() if r not in known) or "none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
