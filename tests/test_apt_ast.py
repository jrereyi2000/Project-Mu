"""tools/mod_builder/apt_ast.py on a synthetic BGFA scene laid out like corenode.ast."""
import zlib

import pytest

from tools.mod_builder import apt_ast


def _pad8(n: int) -> int:
    return (n + 7) & ~7


def make_scene(block2_raw: bytes) -> bytes:
    import random
    rng = random.Random(7)
    block1_raw = bytes(rng.randrange(256) for _ in range(3000)) + b"Apt Data:1:7:8\x00" * 40
    s1 = zlib.compress(block1_raw, 9)
    s2 = zlib.compress(block2_raw, 9)
    header = bytearray(apt_ast.MAGIC + b"\x03\x00\x00\x00\x02\x00\x00\x00" + b"\x00" * (0x70 - 16))
    header[0x54:0x56] = len(s2).to_bytes(2, "little")
    header[0x65:0x67] = len(s1).to_bytes(2, "little")
    off2 = _pad8(0x70 + len(s1))
    return bytes(header) + s1 + b"\x00" * (off2 - 0x70 - len(s1)) + s2


def block2() -> bytes:
    filler = b"".join(b"__Packages.logic_com.C%d\x00\x00\x00\x00\x00\x00" % i for i in range(400))
    return (filler + b"MENU_MAIN\x00\x00\x00\x00\x00\x00\x00" + apt_ast.RAIL_ANCHOR + apt_ast.RAIL_VANILLA
            + b"\x00\x00\x00\x00GetGlobalAdapter\x00" + filler[:2000] + apt_ast.FOCUS_ANCHOR
            + apt_ast.FOCUS_VANILLA + b"\x00PostAttemptUnloadGate\x00" + filler[:4000])


def test_read_blocks_walks_aligned_blocks():
    data = make_scene(block2())
    blocks = apt_ast.read_blocks(data)
    assert len(blocks) == 2
    assert blocks[0].offset == 0x70 and blocks[1].offset % 8 == 0
    assert blocks[1].offset + blocks[1].compressed == len(data)
    assert blocks[1].raw == block2()


def test_franchise_menu_keeps_size_header_and_offsets():
    data = make_scene(block2())
    out, report = apt_ast.franchise_menu(data)
    assert len(out) == len(data)
    b0, b1 = apt_ast.read_blocks(data)
    n0, n1 = apt_ast.read_blocks(out)
    assert out[:b1.offset] == data[:b1.offset]          # header and block 1 byte-identical
    assert n1.compressed == b1.compressed and len(n1.raw) == len(b1.raw)
    raw = n1.raw
    rail = raw.index(apt_ast.RAIL_ANCHOR) + len(apt_ast.RAIL_ANCHOR)
    assert raw[rail:rail + len(apt_ast.RAIL_VANILLA) + 1] == (
        apt_ast.RAIL_FRANCHISE + b"\x00" * (len(apt_ast.RAIL_VANILLA) - len(apt_ast.RAIL_FRANCHISE) + 1))
    focus = raw.index(apt_ast.FOCUS_ANCHOR) + len(apt_ast.FOCUS_ANCHOR)
    assert raw[focus:focus + len(apt_ast.FOCUS_FRANCHISE) + 1] == apt_ast.FOCUS_FRANCHISE + b"\x00"
    # everything outside the two constants is unchanged
    diff = [i for i in range(len(raw)) if raw[i] != b1.raw[i]]
    span_r = range(rail, rail + len(apt_ast.RAIL_VANILLA))
    span_f = range(focus, focus + len(apt_ast.FOCUS_VANILLA))
    assert diff and all(i in span_r or i in span_f for i in diff)
    assert report["sha256_out"] != report["sha256_vanilla"]


def test_deflate_exact_hits_target_and_round_trips():
    raw = b"abc" * 5000 + bytes(range(256)) * 20
    natural = len(zlib.compress(raw, 9))
    for target in (natural + 16, natural + 40):  # a sync flush + stored block costs ~10 bytes
        s = apt_ast.deflate_exact(raw, target)
        assert len(s) == target and zlib.decompress(s) == raw and s[:2] == b"\x78\xda"


def test_replace_const_rejects_longer_missing_and_duplicate():
    raw = b"K\x00old_value\x00tail"
    with pytest.raises(apt_ast.AstError):
        apt_ast.replace_const(raw, b"K\x00", b"old_value", b"much_longer_value")
    with pytest.raises(apt_ast.AstError):
        apt_ast.replace_const(raw, b"K\x00", b"absent", b"x")
    with pytest.raises(apt_ast.AstError):
        apt_ast.replace_const(raw + raw, b"K\x00", b"old_value", b"new")
    out, at = apt_ast.replace_const(raw, b"K\x00", b"old_value", b"new")
    assert out == b"K\x00new\x00\x00\x00\x00\x00\x00\x00tail" and at == 2


def test_not_bgfa_is_refused():
    with pytest.raises(apt_ast.AstError):
        apt_ast.read_blocks(b"NOTBGFA0" + b"\x00" * 200)
