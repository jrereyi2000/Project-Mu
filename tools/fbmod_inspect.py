"""Take a Frosty .fbmod (MMC Mod Manager, Madden 27) apart: header, every resource, every payload.

Parses the whole file (format version 9, as written by MMC Editor 1.1.0.x): the mod details, each
resource record (ebx / res / chunk / bundle / fsfile / embedded icon and screenshots, with the
type-specific fields), and the data table that holds each payload's offset and size. Each resource is
classified from its type, res type and asset name (texture, uniform, audio, UI, gameplay tuning,
legacy file, ...), and chunks are linked to the texture or legacy file they belong to through the
chunk's h32 name hash. The format is documented in docs/reference/MOD_REVERSE_ENGINEERING.md.

With --extract, every payload is written out. Payloads that start with `FMENC001` are encrypted by
MMC Editor (every payload in every real mod checked on 2026-10-30); they are written as-is and
reported, not decoded. Plain payloads are decoded where possible: Frostbite block compression
(zstd, zlib, LZ4 if the `lz4` package is installed, uncompressed blocks; Oodle is reported), bare
zstd and zlib streams; DDS, PNG and EBX are recognised by magic, and DDS is converted to PNG.

    python3 -m tools.fbmod_inspect <file.fbmod> [--extract OUT] [--json] [--all]
"""
import argparse
import io
import json
import re
import struct
import sys
import zlib
from collections import Counter
from pathlib import Path

MAGIC = b"FROSTY\x00\x01"
ENCRYPTED_MAGIC = b"FMENC001"
TYPES = {0: "embedded", 1: "ebx", 2: "res", 3: "chunk", 4: "bundle", 5: "fsfile", 6: "legacy"}

# Frostbite res types seen in the PC_MODS mods (2026-10-30). Names without "?" are confirmed by the asset
# names that carry them (Texture: chunks hash-link to them; MeshSet: *_mesh; SoundWave: sound/..._nwa;
# BundleRefTable: *_brt, merged by a handler; ShaderBlockDepot: *_shaderstate/shaderblock). Names with "?"
# are guesses from the only asset folders they appear in.
RES_TYPES = {
    0x6BDE20BA: "Texture",
    0x49B156D4: "MeshSet",
    0xB2C465F6: "SoundWave",
    0x428EC9D4: "BundleRefTable",
    0xE2E6955B: "BundleRefTable",
    0x73312045: "ShaderBlockDepot",
    0x2D254A89: "ShaderState?",
    0x063994C5: "Font?",
    0xE113D4B1: "PresentationFlow?",
    0x63471787: "PartLoader?",
    0x85EA8656: "ClothWrapping?",
    0x85AC783D: "ClothSim?",
}

# Asset-name keywords -> plain-language category, first match wins.
CATEGORIES = [
    (r"/uniform/|/uniforms/|jersey|_pants|/helmets?/|/socks?|/cleats?/|/gloves?/|/torsogear/|/parts/|towel",
     "uniform / gear"),
    (r"football/texture|/football_", "football"),
    (r"playart|firstdown|linecolor", "field overlay"),
    (r"scorebug|scorehud|/hud/|kickmeter|kick_meter|bottomlineticker", "HUD / scorebug"),
    (r"/fonts?/|fontstyle", "font"),
    (r"logo", "logo"),
    (r"^sound/|/sound/|audio|music|/vo/|_vo_|commentary", "audio"),
    (r"crowd", "crowd"),
    (r"stadium|jumbotron|ribbon|/venue", "stadium"),
    (r"camera|cinematic|cutscene|/cine", "camera / cutscene"),
    (r"presentation|broadcast|replay|/banners/", "presentation"),
    (r"/players?/|playerhead|/heads?/|hair|cyberface", "player head / likeness"),
    (r"supersim|attribsys|antifreeze|/dsub/|btree|behavio|tuning|gameplay|/ai/|playcall|/plays?/|playbook|"
     r"playlibrary|gamesheet|formation", "gameplay / sim tuning"),
    (r"franchise|roster|salary|contract|draft|career", "franchise data"),
    (r"lighting|grass|turf|visualenvironment|postprocess|filmgrain|dof|blur|weather|/fx/", "visuals / lighting"),
    (r"^ui/|/ui/|/node_|menu|frontend|_fe/", "UI / menus"),
]


def _cstr(buf, pos):
    end = buf.index(b"\0", pos)
    return buf[pos:end].decode("latin-1"), end + 1


def fnv1(name):
    """Frosty's name hash ("Fnv1": 5381, multiply by 33, xor byte). A chunk's h32 is this hash of its owner."""
    h = 5381
    for c in name.encode("latin-1"):
        h = ((h * 33) ^ c) & 0xFFFFFFFF
    return h


def _read_resource(buf, pos):
    """One resource record starting at pos -> (dict, next pos)."""
    rtype = buf[pos]
    index, = struct.unpack_from("<i", buf, pos + 1)
    name, pos = _cstr(buf, pos + 5)
    res = {"type": TYPES.get(rtype, f"type{rtype}"), "type_id": rtype, "index": index, "name": name,
           "sha1": None, "size": 0, "flags": 0, "handler": 0, "user_data": ""}
    if index != -1:
        res["sha1"] = buf[pos:pos + 20].hex()
        res["size"], res["flags"], res["handler"] = struct.unpack_from("<qBI", buf, pos + 20)
        res["user_data"], pos = _cstr(buf, pos + 33)
    count, = struct.unpack_from("<i", buf, pos)
    res["bundles"] = list(struct.unpack_from(f"<{count}I", buf, pos + 4))
    pos += 4 + 4 * count
    if rtype == 2:  # res: resType, resRid, resMeta
        res_type, rid, meta_len = struct.unpack_from("<IQi", buf, pos)
        res.update(res_type=res_type, res_type_name=RES_TYPES.get(res_type, f"0x{res_type:08X}"),
                   res_rid=f"{rid:016x}", res_meta=buf[pos + 16:pos + 16 + meta_len].hex())
        pos += 16 + meta_len
    elif rtype == 3:  # chunk: range, logical offset/size, h32 (twice), unknown hash; extras when no handler
        (res["range_start"], res["range_end"], res["logical_offset"], res["logical_size"],
         res["h32"], res["h32_2"], res["unknown_hash"]) = struct.unpack_from("<7I", buf, pos)
        pos += 28
        if res["handler"] == 0:
            first_mip, n = struct.unpack_from("<ii", buf, pos)
            res["first_mip"] = first_mip
            res["extra"] = list(struct.unpack_from(f"<{n}i", buf, pos + 8))
            pos += 8 + 4 * n
    elif rtype == 4:  # bundle: name again + super bundle hash
        res["bundle_name"], pos = _cstr(buf, pos)
        res["super_bundle"], = struct.unpack_from("<I", buf, pos)
        pos += 4
    return res, pos


def parse_fbmod(path_or_bytes):
    """Whole-file parse of a .fbmod: details, resources (with data offsets), header layout."""
    buf = path_or_bytes if isinstance(path_or_bytes, (bytes, bytearray)) else Path(path_or_bytes).read_bytes()
    if buf[:8] != MAGIC:
        raise ValueError("not a Frosty mod (no FROSTY magic)")
    version, = struct.unpack_from("<I", buf, 8)
    data_offset, data_count = struct.unpack_from("<qi", buf, 12)
    pos = 24
    plen = buf[pos]  # 7-bit length prefix; profile names are short enough for one byte
    profile = buf[pos + 1:pos + 1 + plen].decode("latin-1")
    pos += 1 + plen
    game_version, = struct.unpack_from("<I", buf, pos)
    header_hash = buf[pos + 4:pos + 24].hex()
    pos += 24
    details = {}
    for key in ("title", "author", "category", "version", "description", "link"):
        details[key], pos = _cstr(buf, pos)
    count, = struct.unpack_from("<i", buf, pos)
    pos += 4
    resources = []
    for _ in range(count):
        res, pos = _read_resource(buf, pos)
        resources.append(res)
    if pos != data_offset:
        raise ValueError(f"resource list ends at {pos}, data section starts at {data_offset}")
    table = [struct.unpack_from("<qq", buf, data_offset + 16 * i) for i in range(data_count)]
    base = data_offset + 16 * data_count
    for res in resources:
        if 0 <= res["index"] < data_count:
            off, size = table[res["index"]]
            res["data_offset"], res["data_size"] = base + off, size
            res["payload_magic"] = bytes(buf[base + off:base + off + 8])
    mod = {"format": version, "profile": profile, "game_version": game_version, "header_hash": header_hash,
           **details, "data_offset": data_offset, "data_count": data_count, "file_size": len(buf),
           "resources": resources}
    _annotate(mod)
    return mod


def classify(res):
    """Plain-language category for one resource."""
    name = res["name"].lower()
    if res["type"] == "embedded":
        return "mod icon / screenshot"
    if res["user_data"].startswith("legacy;Collector"):
        return "legacy file list (merged)"
    if res["user_data"].startswith("legacy;"):
        path = res["user_data"][7:].lower()
        ext = path.rsplit(".", 1)[-1] if "." in path.rsplit("/", 1)[-1] else ""
        kind = {"db": "database table", "ftc": "franchise tuning (.ftc)", "ast": "UI scene (.ast)", "lua": "script",
                "ini": "config", "txt": "text", "json": "json", "xml": "xml", "csv": "table"}.get(ext, "file")
        return f"legacy {kind}"
    if res["type"] == "fsfile":
        return "initfs file (config / script)"
    if res["type"] == "bundle":
        return "bundle (adds an asset to a bundle)"
    if res.get("res_type_name") == "BundleRefTable" or (res["type"] == "res" and res["handler"]):
        return "bundle ref table (merged)"
    owner = res.get("owner")
    if res["type"] == "chunk":
        if owner:
            return f"{owner['category']} data" if owner["category"] != "texture" else "texture pixels"
        return "chunk (raw data; owner not in this mod)"
    base = None
    for pattern, label in CATEGORIES:
        if re.search(pattern, name):
            base = label
            break
    if res["type"] == "res":
        kind = res.get("res_type_name", "")
        if kind == "Texture":
            return f"texture ({base})" if base else "texture"
        if kind.startswith("SoundWave"):
            return "audio"
        return f"{kind} ({base})" if base else kind
    return base or "ebx asset"


def _annotate(mod):
    """Link chunks to their owner (h32 = fnv1 of the owning texture/legacy name) and classify everything."""
    owners = {}
    for res in mod["resources"]:
        if res["type"] in ("res", "ebx"):
            owners.setdefault(fnv1(res["name"].lower()), res)
        if res["user_data"].startswith("legacy;") and not res["user_data"].startswith("legacy;Collector"):
            owners.setdefault(fnv1(res["user_data"][7:].lower()), {"name": res["user_data"][7:], "type": "legacy"})
    for res in mod["resources"]:
        if res["type"] != "chunk":
            res["category"] = classify(res)
    for res in mod["resources"]:
        if res["type"] == "chunk":
            owner = owners.get(res["h32"])
            if owner is not None:
                cat = "texture" if owner.get("res_type_name") == "Texture" else owner.get("category", "legacy file")
                res["owner"] = {"name": owner["name"], "category": cat}
            res["category"] = classify(res)


# ---------------------------------------------------------------- payload decoding

def _lz4_block(data, size):
    try:
        import lz4.block  # optional
    except ImportError:
        return None
    return lz4.block.decompress(data, uncompressed_size=size)


def _zstd(data, size=None):
    try:
        import zstandard
    except ImportError:
        return None
    dctx = zstandard.ZstdDecompressor()
    return dctx.decompress(data, max_output_size=size or 1 << 28)


BLOCK_CODECS = {0x00: "none", 0x02: "zlib", 0x09: "lz4", 0x0F: "zstd", 0x11: "oodle", 0x15: "oodle",
                0x19: "oodle"}


def decompress_blocks(data):
    """Frostbite block compression: 8-byte big-endian block headers (decompressed size, codec, compressed
    size). Returns (bytes or None, codec names, note). None when the data isn't block-compressed or a block
    can't be decoded here (Oodle, missing lz4)."""
    out, codecs, pos = bytearray(), [], 0
    while pos < len(data):
        if pos + 8 > len(data):
            return None, codecs, "truncated block header"
        dsize, code, csize = struct.unpack_from(">IHH", data, pos)
        dsize &= 0x00FFFFFF
        ctype = (code >> 8) & 0x7F
        csize += (code & 0x0F) << 16
        codec = BLOCK_CODECS.get(ctype)
        if codec is None or dsize == 0 or dsize > 1 << 24:
            return None, codecs, "not Frostbite block compression"
        codecs.append(codec)
        body = data[pos + 8:pos + 8 + (dsize if codec == "none" else csize)]
        pos += 8 + len(body)
        if codec == "none":
            block = body
        elif codec == "zlib":
            block = zlib.decompress(body)
        elif codec == "zstd":
            block = _zstd(body, dsize)
        elif codec == "lz4":
            block = _lz4_block(body, dsize)
            if block is None:
                return None, codecs, "LZ4 block: pip install lz4 to decode"
        else:
            return None, codecs, ("Oodle block: needs Oodle (oo2core_*_win64.dll from the game folder, "
                                  "Windows or Wine) to decode")
        if block is None or len(block) != dsize:
            return None, codecs, f"{codec} block failed to decode"
        out += block
    return bytes(out), codecs, "ok"


def sniff(data):
    """Format name from magic bytes."""
    if data[:4] == b"DDS ":
        return "dds"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:11] == b"EBX":
        return "ebx"
    if data[:4] in (b"\xce\xd1\xb2\x0f", b"\xce\xd1\xb4\x0f"):
        return "ebx"
    if data[:4] == b"\x28\xb5\x2f\xfd":
        return "zstd"
    if data[:1] == b"\x78" and len(data) > 1 and data[1] in (0x01, 0x5E, 0x9C, 0xDA):
        return "zlib"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:4] == b"FBRB":
        return "fbrb"
    return "bin"


def decode_payload(data):
    """-> {"state": encrypted|decoded|raw, "format", "codecs", "note", "bytes"}."""
    if data[:8] == ENCRYPTED_MAGIC:
        return {"state": "encrypted", "format": "mmc-encrypted", "codecs": [], "bytes": None,
                "note": "MMC Editor encryption (FMENC001 + 16-byte-block ciphertext); "
                        "only the MMC Mod Manager decrypts it"}
    fmt = sniff(data)
    if fmt == "zstd":
        out = _zstd(data)
        return {"state": "decoded", "format": sniff(out), "codecs": ["zstd"], "bytes": out, "note": "zstd stream"}
    if fmt == "zlib":
        try:
            out = zlib.decompress(data)
            return {"state": "decoded", "format": sniff(out), "codecs": ["zlib"], "bytes": out, "note": "zlib stream"}
        except zlib.error:
            pass
    if fmt == "bin":
        out, codecs, note = decompress_blocks(data)
        if out is not None:
            return {"state": "decoded", "format": sniff(out), "codecs": codecs, "bytes": out, "note": note}
        if codecs:
            return {"state": "raw", "format": "frostbite-blocks", "codecs": codecs, "bytes": None, "note": note}
    return {"state": "raw", "format": fmt, "codecs": [], "bytes": data, "note": "plain"}


def dds_to_png(data):
    """PNG bytes for a DDS (Pillow reads BC1/BC2/BC3/BC5/BC7 and plain RGBA), else None."""
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        return None
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def _safe(name, limit=100):
    tail = re.sub(r"[^A-Za-z0-9._-]+", "_", name.replace("/", "__"))
    return tail[-limit:]


def extract(path, out_dir, mod=None):
    """Write every payload to out_dir (once per data entry) plus manifest.json. Returns one row per resource
    that has a payload; resources sharing a data entry point at the same files."""
    buf = Path(path).read_bytes()
    mod = mod or parse_fbmod(buf)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows, done = [], {}
    for res in mod["resources"]:
        if "data_offset" not in res:
            continue
        if res["index"] in done:
            rows.append({**done[res["index"]], "type": res["type"], "name": res["name"]})
            continue
        data = buf[res["data_offset"]:res["data_offset"] + res["data_size"]]
        label = res["user_data"][7:] if res["user_data"].startswith("legacy;") else res["name"]
        stem = f"{res['index']:04d}_{res['type']}_{_safe(label)}"
        info = decode_payload(data)
        written = []
        if info["state"] == "encrypted":
            p = out / f"{stem}.fmenc"
            p.write_bytes(data)
            written.append(p.name)
        else:
            body = info["bytes"] if info["bytes"] is not None else data
            ext = info["format"] if info["format"] not in ("bin", "frostbite-blocks") else "bin"
            p = out / f"{stem}.{ext}"
            p.write_bytes(body)
            written.append(p.name)
            if info["format"] == "dds":
                png = dds_to_png(body)
                if png:
                    (out / f"{stem}.png").write_bytes(png)
                    written.append(f"{stem}.png")
        row = {"index": res["index"], "type": res["type"], "name": res["name"], "state": info["state"],
               "format": info["format"], "codecs": info["codecs"], "note": info["note"], "files": written}
        done[res["index"]] = row
        rows.append(row)
    (out / "manifest.json").write_text(json.dumps({"mod": _jsonable(mod), "payloads": rows}, indent=1))
    return rows


# ---------------------------------------------------------------- report

def _jsonable(mod):
    def fix(v):
        return v.hex() if isinstance(v, (bytes, bytearray)) else v
    return {**{k: fix(v) for k, v in mod.items() if k != "resources"},
            "resources": [{k: fix(v) for k, v in r.items()} for r in mod["resources"]]}


def summary(mod):
    """Counts used by the text report and the skill."""
    res = [r for r in mod["resources"] if r["type"] != "embedded"]
    # Several resources can point at one data entry (identical payloads are stored once), so count by index.
    payloads = {r["index"]: r["payload_magic"] for r in mod["resources"] if "payload_magic" in r}
    return {
        "types": Counter(r["type"] for r in res),
        "categories": Counter(r["category"] for r in res),
        "encrypted": sum(1 for magic in payloads.values() if magic == ENCRYPTED_MAGIC),
        "payloads": len(payloads),
        "shared_payloads": sum(1 for r in mod["resources"] if "payload_magic" in r) - len(payloads),
        "folders": Counter("/".join(r["name"].split("/")[:4]) for r in res if r["type"] in ("ebx", "res")),
    }


def report(mod, show_all=False, limit=40):
    s = summary(mod)
    lines = [
        f"{mod['title']} {mod['version']}  by {mod['author']}  [{mod['category']}]",
        f"  profile {mod['profile']}, game build {mod['game_version']}, format v{mod['format']}, "
        f"{mod['file_size']:,} bytes, {mod['data_count']} payloads ({s['encrypted']} encrypted"
        + (f", {s['shared_payloads']} resources reuse another's payload" if s["shared_payloads"] else "") + ")",
    ]
    if mod["description"]:
        lines.append(f"  {mod['description']}")
    if mod["link"]:
        lines.append(f"  link: {mod['link']}")
    lines.append("  types: " + ", ".join(f"{k} {v}" for k, v in s["types"].most_common()))
    lines.append("  categories: " + ", ".join(f"{k} {v}" for k, v in s["categories"].most_common()))
    lines.append("  top folders: " + ", ".join(f"{k} ({v})" for k, v in s["folders"].most_common(6)))
    lines.append("")
    rows = [r for r in mod["resources"] if r["type"] != "embedded" or "data_offset" in r]
    for r in rows if show_all else rows[:limit]:
        extra = ""
        if r["type"] == "res":
            extra = f" {r['res_type_name']}"
        elif r["type"] == "chunk":
            extra = f" logical {r['logical_size']:,}"
            if r.get("owner"):
                extra += f" -> {r['owner']['name']}"
        label = r["user_data"] if r["user_data"].startswith("legacy;") else r["name"]
        handler = f" handler {r['handler']:08x}" if r["handler"] else ""
        size = f"{r['size']:,}" if r["size"] else "-"
        stored = f"{r.get('data_size', 0):,}"
        lines.append(f"  [{r['index']:>4}] {r['type']:<8} {r['category']:<32} {size:>10} / {stored:>10}"
                     f"{handler}{extra}  {label}")
    if not show_all and len(rows) > limit:
        lines.append(f"  ... {len(rows) - limit} more (--all to list them)")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("fbmod")
    ap.add_argument("--extract", metavar="OUT", help="write every payload (and manifest.json) into OUT")
    ap.add_argument("--json", action="store_true", help="print the full parse as JSON")
    ap.add_argument("--all", action="store_true", help="list every resource, not the first 40")
    args = ap.parse_args(argv)
    mod = parse_fbmod(args.fbmod)
    if args.json:
        json.dump(_jsonable(mod), sys.stdout, indent=1)
        print()
    else:
        print(report(mod, show_all=args.all))
    if args.extract:
        rows = extract(args.fbmod, args.extract, mod)
        states = Counter(r["state"] for r in rows)
        print(f"\nextracted {len(rows)} payloads to {args.extract}: "
              + ", ".join(f"{k} {v}" for k, v in states.items()), file=sys.stderr)
        if states.get("encrypted"):
            print("  encrypted payloads are written as .fmenc and can't be decoded here "
                  "(see docs/reference/MOD_REVERSE_ENGINEERING.md, 'Encryption')", file=sys.stderr)


if __name__ == "__main__":
    main()
