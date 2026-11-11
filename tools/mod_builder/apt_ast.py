"""In-place string edits to a BGFA / EA Apt legacy UI scene (`.ast`), MOD_REVERSE_ENGINEERING §2.6.

A `.ast` is `BGFA1.05` + a header + zlib blocks. Block 1 starts after the header table (`first_block`,
0x70 on corenode.ast); each later block starts at
the previous one's end rounded up to 8 bytes; the last ends flush with the file. The header stores
each block's compressed length as a u16 LE (checked on `corenode.ast`, build 3464873: block 2 at 0x54,
block 1 at 0x65); no checksum of the blocks is stored.

An edit here replaces one null-terminated constant in a block with a string no longer than it,
padding with nulls so no offset in the Apt data moves, then re-deflates the block to **exactly** its
original compressed length, so the header and every block offset stay byte-identical.

    python3 -m tools.mod_builder.apt_ast franchise-menu|franchise-only <vanilla corenode.ast> <out.ast>
"""
from __future__ import annotations

import hashlib
import json
import sys
import zlib
from dataclasses import dataclass

MAGIC = b"BGFA1.05"


class AstError(Exception):
    pass


@dataclass(frozen=True)
class Block:
    offset: int
    compressed: int
    raw: bytes


def first_block(data: bytes) -> int:
    """Block 1's offset: the header table after 0x40 is u32@0x18 bytes long, rounded up to 8
    (0x2a → 0x70 on corenode.ast; 0x28 → 0x68 and 0x130 → 0x170 on other scenes)."""
    return (0x40 + int.from_bytes(data[0x18:0x1c], "little") + 7) & ~7


def read_blocks(data: bytes) -> list[Block]:
    """Walk the zlib blocks from first_block() (8-byte aligned starts) to the end of the file."""
    if data[:8] != MAGIC:
        raise AstError("not a BGFA1.05 scene")
    blocks, off = [], first_block(data)
    while off < len(data):
        o = zlib.decompressobj()
        try:
            raw = o.decompress(data[off:])
        except zlib.error as e:
            raise AstError(f"no zlib stream at {off:#x}: {e}") from None
        if not o.eof:
            raise AstError(f"truncated zlib stream at {off:#x}")
        used = len(data) - off - len(o.unused_data)
        blocks.append(Block(off, used, raw))
        nxt = (off + used + 7) & ~7
        if nxt < len(data) and any(data[off + used:nxt]):
            raise AstError(f"non-zero padding after block at {off:#x}")
        off = nxt
    return blocks


def replace_const(raw: bytes, anchor: bytes, old: bytes, new: bytes) -> tuple[bytes, int]:
    """Replace the constant `old` that directly follows `anchor` (must occur exactly once) with
    `new` padded with nulls to `old`'s length. Returns the new bytes and the constant's offset."""
    if len(new) > len(old):
        raise AstError(f"new constant is longer than the old one ({len(new)} > {len(old)})")
    if b"\x00" in new:
        raise AstError("new constant contains a null")
    needle = anchor + old + b"\x00"
    at = raw.find(needle)
    if at < 0 or raw.find(needle, at + 1) >= 0:
        raise AstError(f"anchor + constant not found exactly once: {old[:40]!r}")
    start = at + len(anchor)
    out = raw[:start] + new + b"\x00" * (len(old) - len(new)) + raw[start + len(old):]
    return out, start


def deflate_exact(raw: bytes, target: int) -> bytes:
    """A zlib stream (78 DA) of exactly `target` bytes that inflates to `raw`.

    Deflates raw[:n-k] at level 9 with a sync flush, then one final stored block with the last k
    bytes, and searches k for the exact length (each extra stored byte adds ~1 byte)."""
    adler = zlib.adler32(raw).to_bytes(4, "big")
    for k in range(0, min(len(raw), 65535) + 1):
        c = zlib.compressobj(9, zlib.DEFLATED, 15, 9)
        body = c.compress(raw[:len(raw) - k]) + c.flush(zlib.Z_SYNC_FLUSH)
        tail = raw[len(raw) - k:]
        stored = b"\x01" + len(tail).to_bytes(2, "little") + (len(tail) ^ 0xFFFF).to_bytes(2, "little") + tail
        stream = body + stored + adler
        if len(stream) == target:
            o = zlib.decompressobj()
            if o.decompress(stream) != raw or not o.eof or o.unused_data:
                raise AstError("exact-length stream failed its read-back")
            return stream
        if len(stream) > target + 64:
            break
    raise AstError(f"no exact-length deflate stream for {target} bytes (edit compresses too differently)")


def edit(data: bytes, block_index: int, edits: list[tuple[bytes, bytes, bytes]]) -> tuple[bytes, list[int]]:
    """Apply (anchor, old, new) constant edits to one block and rebuild the file at the same size."""
    blocks = read_blocks(data)
    b = blocks[block_index]
    raw, offsets = b.raw, []
    for anchor, old, new in edits:
        raw, at = replace_const(raw, anchor, old, new)
        offsets.append(at)
    stream = deflate_exact(raw, b.compressed)
    out = data[:b.offset] + stream + data[b.offset + b.compressed:]
    assert len(out) == len(data)
    return out, offsets


# ---- the Franchise main-menu presets (JR, 2026-11-11)

RAIL_ANCHOR = b"NestedPanelIds\x00\x00"
RAIL_VANILLA = (b"nestedlist_events, nestedlist_ultimateteam, nestedlist_franchise, nestedlist_superstar, "
                b"nestedlist_online, nestedlist_play, nestedlist_trainingcamp, nestedlist_creationcenter, "
                b"EXTRAS, SETTINGS, ACCESSIBILITY, nestedlist_debug, FRANCHISE_INFO, SUPERSTAR_INFO, "
                b"ULTIMATETEAM_INFO")
# Mu Franchise Menu 1.1: v1.0 (verified in game 2026-11-11) without Extras and Accessibility.
RAIL_FRANCHISE = b"nestedlist_franchise, nestedlist_play, nestedlist_creationcenter, SETTINGS, nestedlist_debug, FRANCHISE_INFO"
# Mu Franchise Only: the rail is the Franchise tab and its info panel, nothing else (no Settings).
RAIL_FRANCHISE_ONLY = b"nestedlist_franchise, FRANCHISE_INFO"
FOCUS_ANCHOR = b"FocusedRootId\x00\x00\x00"
FOCUS_VANILLA = b"nestedlist_ultimateteam"
FOCUS_FRANCHISE = b"nestedlist_franchise"
PRESETS = {"franchise-menu": RAIL_FRANCHISE, "franchise-only": RAIL_FRANCHISE_ONLY}


def franchise_menu(data: bytes, rail: bytes = RAIL_FRANCHISE) -> tuple[bytes, dict]:
    edits = [(RAIL_ANCHOR, RAIL_VANILLA, rail), (FOCUS_ANCHOR, FOCUS_VANILLA, FOCUS_FRANCHISE)]
    out, offsets = edit(data, 1, edits)
    return out, {
        "size": len(out), "rail_offset": hex(offsets[0]), "focus_offset": hex(offsets[1]),
        "sha256_vanilla": hashlib.sha256(data).hexdigest(), "sha256_out": hashlib.sha256(out).hexdigest(),
    }


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[0] not in PRESETS:
        print(__doc__.strip().splitlines()[-1].strip(), file=sys.stderr)
        return 2
    data = open(argv[1], "rb").read()
    try:
        out, report = franchise_menu(data, PRESETS[argv[0]])
    except AstError as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 1
    open(argv[2], "wb").write(out)
    print(json.dumps({"ok": True, "out": argv[2], **report}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
