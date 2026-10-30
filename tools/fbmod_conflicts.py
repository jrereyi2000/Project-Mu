"""Report assets that more than one Frosty .fbmod edits (MMC Mod Manager, Madden 27).

Reads only each mod's header (format version 9, parsed by tools/fbmod_inspect.py): title,
author, version and the resource list (type, asset name or chunk id, sha1, handler hash). Two mods that
replace the same asset conflict: the one higher in the Mod Manager's applied list
wins. Resources with a handler hash (bundle ref tables, legacy files) are merged
by the Mod Manager instead, so they are reported but not counted as conflicts.

    python3 -m tools.fbmod_conflicts <dir-or-fbmod>... [--exclude SUBSTR ...] [--json]
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from tools.fbmod_inspect import parse_fbmod


def read_fbmod(path):
    """Header of one .fbmod: details and its resource list (resources that carry data)."""
    mod = parse_fbmod(path)
    resources = [{"type": r["type"], "name": r["name"], "sha1": r["sha1"], "handler": r["handler"]}
                 for r in mod["resources"] if r["index"] != -1]
    return {"file": str(path), "format": mod["format"], "profile": mod["profile"], "title": mod["title"],
            "author": mod["author"], "category": mod["category"], "version": mod["version"],
            "resources": resources}


def overlaps(mods):
    """{tuple of mod files: [(type, name, merged, identical), ...]} for assets edited by 2+ mods."""
    owners = defaultdict(list)
    for mod in mods:
        for res in mod["resources"]:
            if res["type"] != "embedded":
                owners[(res["type"], res["name"])].append((mod["file"], res["handler"], res["sha1"]))
    groups = defaultdict(list)
    for (rtype, name), hits in owners.items():
        files = tuple(sorted({h[0] for h in hits}))
        if len(files) > 1:
            groups[files].append((rtype, name, all(h[1] for h in hits), len({h[2] for h in hits}) == 1))
    return groups


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--exclude", action="append", default=[], help="skip files whose path contains this")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    files = []
    for p in map(Path, args.paths):
        files += sorted(p.rglob("*.fbmod")) if p.is_dir() else [p]
    files = [f for f in files if not any(x in str(f) for x in args.exclude)]
    mods = []
    for f in files:
        try:
            mods.append(read_fbmod(f))
        except Exception as exc:  # a mod we can't parse is reported, not fatal
            print(f"skip {f}: {exc}", file=sys.stderr)
    groups = overlaps(mods)
    if args.json:
        json.dump({"mods": mods, "overlaps": [{"files": list(k), "assets": v} for k, v in groups.items()]},
                  sys.stdout, indent=1)
        return
    for mod in mods:
        print(f"{Path(mod['file']).name}: {mod['title']} {mod['version']} ({mod['author']}), "
              f"{len(mod['resources'])} resources")
    print()
    for files, assets in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        conflicting = [a for a in assets if not a[2]]
        label = "CONFLICT" if conflicting else "merged"
        same = sum(1 for a in assets if a[3])
        print(f"{label}: {len(assets)} shared assets ({same} identical) :: "
              + " | ".join(Path(f).name for f in files))
        for rtype, name, _, _ in assets[:3]:
            print(f"    {rtype} {name}")


if __name__ == "__main__":
    main()
