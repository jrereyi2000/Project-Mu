"""Report assets that more than one Frosty .fbmod edits (MMC Mod Manager, Madden 27).

Reads only each mod's header (format version 9): title, author, version and the
resource list (type, asset name or chunk id, sha1, handler hash). Two mods that
replace the same asset conflict: the one higher in the Mod Manager's applied list
wins. Resources with a handler hash (bundle ref tables, legacy files) are merged
by the Mod Manager instead, so they are reported but not counted as conflicts.

    python3 -m tools.fbmod_conflicts <dir-or-fbmod>... [--exclude SUBSTR ...] [--json]
"""
import argparse
import json
import re
import struct
import sys
from collections import defaultdict
from pathlib import Path

TYPES = {0: "embedded", 1: "ebx", 2: "res", 3: "chunk", 4: "bundle", 5: "fsfile", 6: "legacy"}


def _cstr(buf, pos):
    end = buf.index(b"\0", pos)
    return buf[pos:end].decode("latin-1"), end + 1


def read_fbmod(path):
    """Header of one .fbmod: details and its resource list."""
    buf = Path(path).read_bytes()
    if buf[:6] != b"FROSTY":
        raise ValueError(f"{path}: not a Frosty mod")
    version, = struct.unpack_from("<I", buf, 8)
    data_offset, = struct.unpack_from("<q", buf, 12)
    header = buf[:data_offset]
    profile = header[25:25 + header[24]].decode("latin-1")
    # gameVersion and a hash follow the profile name; the details are the next C strings
    title_at = re.compile(rb"[\x20-\x7e]{2,}\x00").search(header, 25 + header[24] + 4).start()
    title, pos = _cstr(header, title_at)
    author, pos = _cstr(header, pos)
    category, pos = _cstr(header, pos)
    mod_version, pos = _cstr(header, pos)
    # Resources carry consecutive indices: <type byte><int32 index><name>\0<sha1><size>...
    resources = []
    index = 0
    while True:
        pattern = re.compile(re.escape(struct.pack("<i", index)) + rb"([\x20-\x7e]{2,})\x00")
        match = next((m for m in pattern.finditer(header, pos) if header[m.start() - 1] in TYPES), None)
        if match is None:
            break
        end = match.end()
        handler, = struct.unpack_from("<I", header, end + 29)
        resources.append({
            "type": TYPES[header[match.start() - 1]],
            "name": match.group(1).decode("latin-1"),
            "sha1": header[end:end + 20].hex(),
            "handler": handler,
        })
        pos = end
        index += 1
    return {"file": str(path), "format": version, "profile": profile, "title": title.lstrip("UO)k"),
            "author": author, "category": category, "version": mod_version, "resources": resources}


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
