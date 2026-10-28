"""
CharacterVisuals.RawData ISON decoder, encoder, and skinTone updater for Madden 27 PC franchise saves
(docs/done/PRE_PC_WORK.md §3, docs/in_progress/PC_MIGRATION_PLAN.md §4.3 "Appearance").

CharacterVisuals.RawData storage format:
  In Madden 27 franchise saves, CharacterVisuals is a table with Table3 (binary blob) data.
  The field RawData in Table3 is formatted as:
    - 2 bytes unsigned 16-bit integer (little-endian): byte length of the zstd compressed stream.
    - Zstandard compressed stream (starts with magic 0x28 0xb5 0x2f 0xfd).
    - Compression uses a predefined 20KB trained zstd dictionary with dictionary ID 0x1823777C
      (stored in madden-franchise at data/zstd-dicts/27/dict.bin).
    - Following the zstd stream, the buffer is padded with trailing null bytes (0x00) up to
      the Table3 field's maximum capacity (maxLength + 2 = 377 bytes).

ISON Document format:
  When decompressed, the payload is an EA ISON (Internal Serializable Object Notation) document:
    - Header: 0x0D
    - Types:
        0x0F: Object start (followed by key-value pairs until 0x13)
        0x13: Object end
        0x0E: Array start (followed by items until 0x12)
        0x12: Array end
        0x10: KeyValuePair marker (precedes a key and value)
        0x0A: Interned string (followed by 2 bytes uint16 LE index into interned-strings lookup.json)
        0x0B: Full string (followed by 4 bytes uint32 LE length, then UTF-8 string bytes)
        0x09: Double (followed by 8 bytes float64 LE)
        0x03: Byte / unsigned byte (followed by 1 byte uint8)
        0x00: Signed byte (followed by 1 byte int8)
        0x11: ISON terminator (end of document)

How skinTone is stored:
  Inside the root object, skinTone is stored as a KeyValuePair:
    0x10 (KEYVALUEPAIR)
    0x0A 0x20 0x00 (INTERNED_STRING key 32 = 'skinTone')
    0x03 (BYTE)
    <tone: uint8 (1-8)>
  In 2,985 of 2,987 records in CAREER-GIANTS-AUTOSAVE, skinTone is present at byte offset 581
  as the sequence [0x10, 0x0a, 0x20, 0x00, 0x03, <tone>].

Head Tone to Body Tone Mapping:
  Analysis of real players in CAREER-GIANTS-AUTOSAVE shows a direct 1-to-1 modal correspondence
  between the first digit of GenericHeadAssetName (1-7) and CharacterVisuals.RawData skinTone (1-8):
    Head Tone 1 -> Body skinTone 1 (63.0%)
    Head Tone 2 -> Body skinTone 2 (63.5%)
    Head Tone 3 -> Body skinTone 3 (35.4%)
    Head Tone 4 -> Body skinTone 4 (50.2%)
    Head Tone 5 -> Body skinTone 5 (48.1%)
    Head Tone 6 -> Body skinTone 6 (55.4%)
    Head Tone 7 -> Body skinTone 7 (68.3%)
"""

import json
import os
import struct
import sys
from pathlib import Path

import zstandard as zstd

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DICT_PATH = (
    REPO_ROOT
    / "pipeline"
    / "save_reader"
    / "node_modules"
    / "madden-franchise"
    / "data"
    / "zstd-dicts"
    / "27"
    / "dict.bin"
)
DEFAULT_LOOKUP_PATH = (
    REPO_ROOT
    / "pipeline"
    / "save_reader"
    / "node_modules"
    / "madden-franchise"
    / "data"
    / "interned-strings"
    / "27"
    / "lookup.json"
)

# Modal mapping from GenericHeadAssetName tone digit (1-7) to body skinTone (1-8)
HEAD_TO_BODY_SKIN_TONE = {
    1: 1,
    2: 2,
    3: 3,
    4: 4,
    5: 5,
    6: 6,
    7: 7,
}

# ISON Constants
ISON_HEADER = 0x0D
ISON_OBJECT_START = 0x0F
ISON_OBJECT_END = 0x13
ISON_ARRAY_START = 0x0E
ISON_ARRAY_END = 0x12
ISON_INTERNED_STRING = 0x0A
ISON_STRING = 0x0B
ISON_KEYVALUEPAIR = 0x10
ISON_DOUBLE = 0x09
ISON_BYTE = 0x03
ISON_SIGNED_BYTE = 0x00
ISON_END = 0x11

# Interned string index for 'skinTone' in Madden 27
SKIN_TONE_KEY_INDEX = 32
SKIN_TONE_PATTERN = bytes([ISON_KEYVALUEPAIR, ISON_INTERNED_STRING, SKIN_TONE_KEY_INDEX & 0xFF, (SKIN_TONE_KEY_INDEX >> 8) & 0xFF, ISON_BYTE])
ZSTD_MAGIC = bytes([0x28, 0xB5, 0x2F, 0xFD])

_dict_cache = None
_lookup_cache = None
_reverse_lookup_cache = None


def get_zstd_dict(dict_path=None):
    """Loads and caches the ZstdCompressionDict."""
    global _dict_cache
    if _dict_cache is None:
        p = Path(dict_path) if dict_path else DEFAULT_DICT_PATH
        if not p.exists():
            raise FileNotFoundError(f"Zstd dictionary not found at {p}")
        with open(p, "rb") as f:
            _dict_cache = zstd.ZstdCompressionDict(f.read())
    return _dict_cache


def get_lookups(lookup_path=None):
    """Loads and caches interned string lookup dicts."""
    global _lookup_cache, _reverse_lookup_cache
    if _lookup_cache is None:
        p = Path(lookup_path) if lookup_path else DEFAULT_LOOKUP_PATH
        if not p.exists():
            raise FileNotFoundError(f"Interned string lookup not found at {p}")
        with open(p, "r", encoding="utf-8") as f:
            raw = json.load(f)
        _lookup_cache = {int(k): v for k, v in raw.items()}
        _reverse_lookup_cache = {v.lower(): k for k, v in _lookup_cache.items()}
    return _lookup_cache, _reverse_lookup_cache


def decompress_slice(slice_bytes: bytes, dict_data=None, max_output_size: int = 1024 * 1024) -> bytes:
    """Decompresses a zstd-compressed ISON slice using the Madden 27 dictionary."""
    zd = dict_data or get_zstd_dict()
    dctx = zstd.ZstdDecompressor(dict_data=zd)
    return dctx.decompress(slice_bytes, max_output_size=max_output_size)


def compress_slice(decompressed: bytes, dict_data=None, level: int = 19) -> bytes:
    """Compresses an ISON byte buffer with the Madden 27 dictionary."""
    zd = dict_data or get_zstd_dict()
    cctx = zstd.ZstdCompressor(dict_data=zd, level=level, write_content_size=True)
    return cctx.compress(decompressed)


def parse_ison(buf: bytes, lookup=None) -> dict:
    """Parses an ISON buffer into a Python dictionary."""
    if not lookup:
        lookup, _ = get_lookups()

    pos = 0
    if buf[pos] != ISON_HEADER:
        raise ValueError(f"Invalid ISON header: {hex(buf[pos])}")
    pos += 1

    def read_val():
        nonlocal pos
        vtype = buf[pos]
        pos += 1
        if vtype == ISON_INTERNED_STRING:
            key = struct.unpack_from("<H", buf, pos)[0]
            pos += 2
            return lookup.get(key, "UnkString")
        elif vtype == ISON_STRING:
            slen = struct.unpack_from("<I", buf, pos)[0]
            pos += 4
            s = buf[pos : pos + slen].decode("utf-8")
            pos += slen
            return s
        elif vtype == ISON_DOUBLE:
            val = struct.unpack_from("<d", buf, pos)[0]
            pos += 8
            return val
        elif vtype == ISON_BYTE:
            val = buf[pos]
            pos += 1
            return val
        elif vtype == ISON_SIGNED_BYTE:
            val = struct.unpack_from("<b", buf, pos)[0]
            pos += 1
            return val
        elif vtype == ISON_OBJECT_START:
            obj = {}
            while buf[pos] != ISON_OBJECT_END:
                if buf[pos] != ISON_KEYVALUEPAIR:
                    raise ValueError(f"Expected KeyValuePair marker at {pos}, got {hex(buf[pos])}")
                pos += 1
                k = read_val()
                v = read_val()
                obj[k] = v
            pos += 1  # Skip ISON_OBJECT_END
            return obj
        elif vtype == ISON_ARRAY_START:
            arr = []
            while buf[pos] != ISON_ARRAY_END:
                arr.append(read_val())
            pos += 1  # Skip ISON_ARRAY_END
            return arr
        else:
            raise ValueError(f"Unknown ISON type: {hex(vtype)} at offset {pos}")

    res = read_val()
    if pos < len(buf) and buf[pos] != ISON_END:
        raise ValueError(f"Expected ISON terminator at offset {pos}, got {hex(buf[pos])}")
    return res


def write_ison(val: dict, reverse_lookup=None) -> bytes:
    """Encodes a Python dictionary into an ISON byte buffer."""
    if not reverse_lookup:
        _, reverse_lookup = get_lookups()

    out = bytearray([ISON_HEADER])

    def write_val(v):
        if isinstance(v, dict):
            out.append(ISON_OBJECT_START)
            for k, subv in v.items():
                out.append(ISON_KEYVALUEPAIR)
                k_lower = k.lower()
                if k_lower in reverse_lookup:
                    out.append(ISON_INTERNED_STRING)
                    out.extend(struct.pack("<H", reverse_lookup[k_lower]))
                else:
                    out.append(ISON_STRING)
                    kb = k.encode("utf-8")
                    out.extend(struct.pack("<I", len(kb)))
                    out.extend(kb)
                write_val(subv)
            out.append(ISON_OBJECT_END)
        elif isinstance(v, list):
            out.append(ISON_ARRAY_START)
            for item in v:
                write_val(item)
            out.append(ISON_ARRAY_END)
        elif isinstance(v, str):
            v_lower = v.lower()
            if v_lower in reverse_lookup:
                out.append(ISON_INTERNED_STRING)
                out.extend(struct.pack("<H", reverse_lookup[v_lower]))
            else:
                out.append(ISON_STRING)
                vb = v.encode("utf-8")
                out.extend(struct.pack("<I", len(vb)))
                out.extend(vb)
        elif isinstance(v, float):
            out.append(ISON_DOUBLE)
            out.extend(struct.pack("<d", v))
        elif isinstance(v, bool) or (isinstance(v, int) and v >= 0):
            out.append(ISON_BYTE)
            out.append(v & 0xFF)
        elif isinstance(v, int) and v < 0:
            out.append(ISON_SIGNED_BYTE)
            out.extend(struct.pack("<b", v))
        else:
            raise ValueError(f"Unsupported ISON value type: {type(v)}")

    write_val(val)
    out.append(ISON_END)
    return bytes(out)


def extract_skin_tone(decompressed_ison: bytes) -> int | None:
    """Extracts skinTone (1-8) from decompressed ISON bytes."""
    idx = decompressed_ison.find(SKIN_TONE_PATTERN)
    if idx != -1 and idx + len(SKIN_TONE_PATTERN) < len(decompressed_ison):
        return decompressed_ison[idx + len(SKIN_TONE_PATTERN)]
    # Fallback to full parse if pattern is non-interned
    try:
        data = parse_ison(decompressed_ison)
        tone = data.get("skinTone")
        if isinstance(tone, int) and 1 <= tone <= 8:
            return tone
    except Exception:
        pass
    return None


def set_skin_tone_in_ison(decompressed_ison: bytes, tone: int) -> bytes:
    """Updates skinTone (1-8) in decompressed ISON bytes. Returns new ISON bytes."""
    if not (1 <= tone <= 8):
        raise ValueError(f"skinTone must be 1-8, got {tone}")

    idx = decompressed_ison.find(SKIN_TONE_PATTERN)
    if idx != -1:
        # Fast, exact in-place update of the single tone byte
        val_offset = idx + len(SKIN_TONE_PATTERN)
        res = bytearray(decompressed_ison)
        res[val_offset] = tone
        return bytes(res)

    # If skinTone key is missing from root object, parse and add it
    data = parse_ison(decompressed_ison)
    data["skinTone"] = tone
    return write_ison(data)


def extract_zstd_slice(raw_buffer: bytes) -> tuple[int, bytes]:
    """Extracts (zstd_start_offset, zstd_slice) from raw Table3 buffer."""
    if len(raw_buffer) < 6:
        raise ValueError(f"RawData buffer too short: {len(raw_buffer)} bytes")
    compressed_len = struct.unpack_from("<H", raw_buffer, 0)[0]
    zstd_start = raw_buffer.find(ZSTD_MAGIC)
    if zstd_start == -1:
        raise ValueError("Zstandard magic header not found in buffer")
    if zstd_start + compressed_len > len(raw_buffer):
        raise ValueError(
            f"Buffer bounds error: start {zstd_start} + len {compressed_len} > {len(raw_buffer)}"
        )
    return zstd_start, raw_buffer[zstd_start : zstd_start + compressed_len]


def read_skin_tone_from_raw_data(raw_buffer: bytes, dict_data=None) -> int | None:
    """Decompresses Table3 RawData buffer and reads skinTone."""
    _, slice_bytes = extract_zstd_slice(raw_buffer)
    decompressed = decompress_slice(slice_bytes, dict_data)
    return extract_skin_tone(decompressed)


def update_raw_data_buffer(raw_buffer: bytes, new_tone: int, dict_data=None, level: int = 19) -> bytes:
    """
    Updates skinTone in the Table3 RawData buffer and returns a new buffer
    padded to the original buffer length.
    """
    if not (1 <= new_tone <= 8):
        raise ValueError(f"skinTone must be an integer between 1 and 8, got {new_tone}")

    _, slice_bytes = extract_zstd_slice(raw_buffer)
    decompressed = decompress_slice(slice_bytes, dict_data)
    modified_ison = set_skin_tone_in_ison(decompressed, new_tone)
    new_compressed = compress_slice(modified_ison, dict_data, level=level)

    # Check that compressed stream fits within capacity
    # First 2 bytes are length prefix; total capacity is len(raw_buffer)
    max_payload = len(raw_buffer) - 2
    if len(new_compressed) > max_payload:
        raise ValueError(
            f"Compressed stream ({len(new_compressed)} bytes) exceeds slot capacity ({max_payload} bytes)"
        )

    # Construct new buffer: [2-byte LE length] + [compressed bytes] + [padding zeros]
    new_buf = bytearray(len(raw_buffer))
    struct.pack_into("<H", new_buf, 0, len(new_compressed))
    new_buf[2 : 2 + len(new_compressed)] = new_compressed
    return bytes(new_buf)


# ------------------------------------------------------------------ CLI / Batch ----
def main():
    import argparse

    parser = argparse.ArgumentParser(description="CharacterVisuals RawData processor")
    sub = parser.add_subparsers(dest="command")

    # batch subcommands: JSON requests in, JSON results out. visual_edits.mjs passes files (--in/--out): a
    # stdin pipe from Node's spawnSync could hang without EOF when the writer itself ran under pytest
    # (PRE_PC_WORK.md R7). Without them, stdin -> stdout.
    for name, help_text in (("batch", "Batch process CharacterVisuals edits"),
                            ("read-batch", "Batch read skin tones")):
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("--in", dest="in_path", help="request JSON file (default stdin)")
        sp.add_argument("--out", dest="out_path", help="result JSON file (default stdout)")

    args = parser.parse_args()

    def load_requests():
        if args.in_path:
            with open(args.in_path) as fh:
                return json.load(fh)
        return json.load(sys.stdin)

    def dump_results(results):
        if args.out_path:
            with open(args.out_path, "w") as fh:
                json.dump(results, fh)
        else:
            json.dump(results, sys.stdout)

    if args.command == "batch":
        # [{"id": 0, "raw_hex": "...", "skinTone": 3}, ...]
        reqs = load_requests()
        zd = get_zstd_dict()
        results = []
        for req in reqs:
            raw_buf = bytes.fromhex(req["raw_hex"])
            tone = int(req["skinTone"])
            old_tone = read_skin_tone_from_raw_data(raw_buf, zd)
            new_buf = update_raw_data_buffer(raw_buf, tone, zd)
            results.append({
                "id": req.get("id"),
                "old_tone": old_tone,
                "new_tone": tone,
                "new_raw_hex": new_buf.hex(),
            })
        dump_results(results)
    elif args.command == "read-batch":
        reqs = load_requests()
        zd = get_zstd_dict()
        results = []
        for req in reqs:
            raw_buf = bytes.fromhex(req["raw_hex"])
            try:
                tone = read_skin_tone_from_raw_data(raw_buf, zd)
            except Exception as e:
                sys.stderr.write(f"Failed on req {req.get('id')}: {e}, len={len(raw_buf)}, prefix={raw_buf[:20].hex()}\n")
                raise
            results.append({
                "id": req.get("id"),
                "skinTone": tone,
            })
        dump_results(results)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
