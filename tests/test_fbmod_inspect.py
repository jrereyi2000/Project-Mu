"""
tests/test_fbmod_inspect.py: Unit tests for tools/fbmod_inspect.py (Frosty .fbmod parser and extractor,
docs/reference/MOD_REVERSE_ENGINEERING.md) and the fbmod_conflicts reader built on it. Every mod here is a
synthetic .fbmod built in the test with the format-9 layout found in the real MMC mods; no real mod files.
"""
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

import numpy as np

from headshots.portrait_converter.dds import encode_dds
from tools import fbmod_conflicts
from tools import fbmod_inspect as fi

TEXTURE = 0x6BDE20BA
TEX_NAME = "content/objects/common/football/texture/football_color"
LEGACY_PATH = "common/database/json/ingameconfig_defaults.json"


def cstr(s):
    return s.encode("latin-1") + b"\0"


def record(rtype, index, name, size=0, flags=0, handler=0, user_data="", bundles=(), sha1=b"\x11" * 20):
    out = bytes([rtype]) + struct.pack("<i", index) + cstr(name)
    if index != -1:
        out += sha1 + struct.pack("<qBI", size, flags, handler) + cstr(user_data)
    return out + struct.pack("<i", len(bundles)) + b"".join(struct.pack("<I", b) for b in bundles)


def chunk_fields(range_end, logical_size, h32, handler=0, first_mip=0, extra=()):
    out = struct.pack("<7I", 0, range_end, 0, logical_size, h32, h32, 0xDEADBEEF)
    if handler == 0:
        out += struct.pack("<ii", first_mip, len(extra)) + b"".join(struct.pack("<i", e) for e in extra)
    return out


def frostbite_block(body, codec):
    """One Frostbite block: BE decompressed size, BE codec code, BE compressed size."""
    if codec == "zlib":
        comp, code = zlib.compress(body), 0x0270
    elif codec == "none":
        comp, code = body, 0x0070
    elif codec == "oodle":
        comp, code = b"\x8c" * 16, 0x1570
    else:
        import zstandard
        comp, code = zstandard.ZstdCompressor().compress(body), 0x0F70
    return struct.pack(">IHH", len(body), code, len(comp)) + comp


def build_fbmod(payloads, records, title="Pink Test", game_version=3468304):
    """A format-9 .fbmod: header, details, resource records, data table, data."""
    details = b"".join(cstr(s) for s in (title, "Tester", "Cosmetic", "1.0", "A synthetic mod", ""))
    body = (bytes([8]) + b"Madden27" + struct.pack("<I", game_version) + b"\xab" * 20
            + details + struct.pack("<i", len(records)) + b"".join(records))
    data_offset = 24 + len(body)
    table, blob = b"", b""
    for p in payloads:
        table += struct.pack("<qq", len(blob), len(p))
        blob += p
    head = fi.MAGIC + struct.pack("<I", 9) + struct.pack("<qi", data_offset, len(payloads))
    return head + body + table + blob


def sample_mod():
    dds = encode_dds(np.full((8, 8, 4), [255, 105, 180, 255], np.uint8), "DDS_BC1")
    payloads = [
        fi.ENCRYPTED_MAGIC + b"\x00" * 64,                    # 0 icon (encrypted)
        fi.ENCRYPTED_MAGIC + b"\x01" * 48,                    # 1 ebx (encrypted)
        frostbite_block(b"T" * 184, "none"),                  # 2 texture res header
        frostbite_block(dds, "zstd"),                         # 3 texture pixels -> DDS
        zlib.compress(b'{"autoplay": true}'),                 # 4 legacy json (zlib stream)
        b"\x00" * 52,                                         # 5 collector list
        frostbite_block(b"x" * 100, "oodle"),                 # 6 oodle block
        b"[Game]\nHalftime=0\n",                              # 7 fsfile
    ]
    records = [
        record(0, 0, "Icon", size=64),
        record(0, -1, "Screenshot0"),
        record(1, 1, "content/footballcharacter/items/uniform/jersey/bears/u_jersey_bears_2023_whi", size=2150),
        # a second ebx reusing payload 1 (identical payloads are stored once)
        record(1, 1, "content/footballcharacter/items/uniform/jersey/bears/u_jersey_bears_2023_bla", size=2150),
        record(2, 2, TEX_NAME, size=184) + struct.pack("<IQi", TEXTURE, 0x1234, 16) + b"\x0c" + b"\0" * 15,
        record(3, 3, "2fdfc2a6-bc8c-1211-0569-d9ea61f087d5")
        + chunk_fields(len(payloads[3]), len(dds), fi.fnv1(TEX_NAME), extra=(26, 13)),
        record(3, 4, "383d5f85-1843-a8a8-1167-4a724f97c001", flags=8, user_data="legacy;" + LEGACY_PATH)
        + chunk_fields(0, 18, fi.fnv1(LEGACY_PATH), first_mip=-1, extra=(9,)),
        record(3, 5, "12723b33-68df-97d8-a361-9f810e933e61", size=52, handler=0xBD9BFB65,
               user_data="legacy;Collector (ContentShared/global/RtamStorages/Gamemode_Common)")
        + chunk_fields(0, 0, 0, handler=0xBD9BFB65),
        record(3, 6, "aaaaaaaa-0000-0000-0000-000000000000") + chunk_fields(0, 100, 0x12345678),
        record(4, -1, "win32/content/characters/player/players/x/x_playerhead_brt")
        + cstr("win32/content/characters/player/players/x/x_playerhead_brt") + struct.pack("<I", 0xAFB94929),
        record(5, 7, "Scripts/LicenseeGame.cfg", size=19),
    ]
    return build_fbmod(payloads, records), dds


class TestParse(unittest.TestCase):
    def setUp(self):
        self.buf, self.dds = sample_mod()
        self.mod = fi.parse_fbmod(self.buf)
        self.res = self.mod["resources"]

    def test_header_and_details(self):
        m = self.mod
        self.assertEqual((m["format"], m["profile"], m["game_version"]), (9, "Madden27", 3468304))
        self.assertEqual(m["header_hash"], "ab" * 20)
        self.assertEqual((m["title"], m["author"], m["category"], m["version"]),
                         ("Pink Test", "Tester", "Cosmetic", "1.0"))
        self.assertEqual(m["description"], "A synthetic mod")
        self.assertEqual(m["data_count"], 8)
        self.assertEqual(len(self.res), 11)

    def test_types_and_fields(self):
        self.assertEqual([r["type"] for r in self.res],
                         ["embedded", "embedded", "ebx", "ebx", "res", "chunk", "chunk", "chunk", "chunk",
                          "bundle", "fsfile"])
        tex = self.res[4]
        self.assertEqual((tex["res_type_name"], tex["res_rid"]), ("Texture", "0000000000001234"))
        self.assertEqual(tex["res_meta"][:2], "0c")
        pixels = self.res[5]
        self.assertEqual(pixels["extra"], [26, 13])
        self.assertEqual(pixels["first_mip"], 0)
        self.assertEqual(pixels["h32"], pixels["h32_2"])
        self.assertNotIn("first_mip", self.res[7])  # handler chunks stop after 7 fields
        self.assertEqual(self.res[9]["super_bundle"], 0xAFB94929)
        self.assertEqual(self.res[1].get("sha1"), None)  # no-data embedded record

    def test_data_table(self):
        for r in self.res:
            if r["index"] >= 0:
                start = r["data_offset"]
                self.assertLessEqual(start + r["data_size"], len(self.buf))
        self.assertEqual(self.res[2]["data_offset"], self.res[3]["data_offset"])
        self.assertEqual(self.res[0]["payload_magic"], fi.ENCRYPTED_MAGIC)

    def test_chunk_owner_and_categories(self):
        self.assertEqual(self.res[5]["owner"]["name"], TEX_NAME)
        self.assertEqual(self.res[5]["category"], "texture pixels")
        self.assertEqual(self.res[6]["owner"]["name"], LEGACY_PATH)
        self.assertEqual(self.res[6]["category"], "legacy json")
        self.assertEqual(self.res[7]["category"], "legacy file list (merged)")
        self.assertEqual(self.res[8]["category"], "chunk (raw data; owner not in this mod)")
        self.assertEqual(self.res[2]["category"], "uniform / gear")
        self.assertEqual(self.res[4]["category"], "texture (football)")
        self.assertEqual(self.res[10]["category"], "initfs file (config / script)")

    def test_fnv1_matches_real_mod_value(self):
        # h32 of the yellow first-down-marker texture chunk in "M27 yellow first down marker.fbmod"
        name = "contentshared/football/playart/textures/linecolors/playartlinecolor_firstdownline"
        self.assertEqual(fi.fnv1(name), 0xF4527E5D)

    def test_summary_counts_shared_payloads_once(self):
        s = fi.summary(self.mod)
        self.assertEqual(s["payloads"], 8)
        self.assertEqual(s["encrypted"], 2)
        self.assertEqual(s["shared_payloads"], 1)

    def test_rejects_bad_files(self):
        with self.assertRaises(ValueError):
            fi.parse_fbmod(b"NOTAMOD!" + b"\0" * 40)
        broken = bytearray(self.buf)
        struct.pack_into("<q", broken, 12, len(broken) - 5)  # data offset doesn't match the records
        with self.assertRaises(ValueError):
            fi.parse_fbmod(bytes(broken))

    def test_report_text(self):
        text = fi.report(self.mod, show_all=True)
        self.assertIn("Pink Test 1.0  by Tester", text)
        self.assertIn("2 encrypted", text)
        self.assertIn(f"-> {TEX_NAME}", text)
        self.assertIn("legacy;" + LEGACY_PATH, text)


class TestDecode(unittest.TestCase):
    def test_encrypted_is_reported_not_decoded(self):
        info = fi.decode_payload(fi.ENCRYPTED_MAGIC + b"\x99" * 32)
        self.assertEqual((info["state"], info["bytes"]), ("encrypted", None))

    def test_frostbite_blocks(self):
        body = b"hello frostbite " * 50
        for codec in ("zstd", "zlib", "none"):
            data = frostbite_block(body[:300], codec) + frostbite_block(body[300:], codec)
            out, codecs, note = fi.decompress_blocks(data)
            self.assertEqual(out, body, codec)
            self.assertEqual(codecs, [codec, codec])

    def test_oodle_is_reported(self):
        info = fi.decode_payload(frostbite_block(b"x" * 100, "oodle"))
        self.assertEqual((info["state"], info["format"]), ("raw", "frostbite-blocks"))
        self.assertIn("Oodle", info["note"])

    def test_sniff(self):
        self.assertEqual(fi.sniff(b"DDS " + b"\0" * 10), "dds")
        self.assertEqual(fi.sniff(b"\x89PNG\r\n\x1a\n"), "png")
        self.assertEqual(fi.sniff(b"RIFF\0\0\0\0EBXD"), "ebx")
        self.assertEqual(fi.sniff(b"\x28\xb5\x2f\xfd"), "zstd")
        self.assertEqual(fi.sniff(b"plain text"), "bin")


class TestExtractAndCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.buf, self.dds = sample_mod()
        self.path = self.dir / "Pink Test.fbmod"
        self.path.write_bytes(self.buf)

    def tearDown(self):
        self.tmp.cleanup()

    def test_extract(self):
        out = self.dir / "out"
        rows = fi.extract(self.path, out)
        files = {f.name for f in out.iterdir()}
        self.assertIn("0000_embedded_Icon.fmenc", files)
        dds_name = [f for f in files if f.startswith("0003_") and f.endswith(".dds")]
        self.assertEqual(len(dds_name), 1)
        self.assertEqual((out / dds_name[0]).read_bytes(), self.dds)
        self.assertTrue(any(f.startswith("0003_") and f.endswith(".png") for f in files))
        self.assertEqual((out / [f for f in files if f.startswith("0004_")][0]).read_bytes(),
                         b'{"autoplay": true}')
        self.assertTrue(any(f.startswith("0007_fsfile") for f in files))
        states = {r["index"]: r["state"] for r in rows}
        self.assertEqual(states[1], "encrypted")
        self.assertEqual(states[3], "decoded")
        self.assertEqual(states[6], "raw")
        # the two ebx share payload 1: one file, two rows
        self.assertEqual(sum(1 for r in rows if r["index"] == 1), 2)
        self.assertEqual(sum(1 for f in files if f.startswith("0001_")), 1)
        manifest = json.loads((out / "manifest.json").read_text())
        self.assertEqual(manifest["mod"]["title"], "Pink Test")

    def test_cli_json(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            fi.main([str(self.path), "--json"])
        data = json.loads(buf.getvalue())
        self.assertEqual(data["resources"][0]["payload_magic"], fi.ENCRYPTED_MAGIC.hex())

    def test_conflicts_reader_uses_full_parse(self):
        mod = fbmod_conflicts.read_fbmod(self.path)
        self.assertEqual(mod["title"], "Pink Test")
        names = [r["name"] for r in mod["resources"]]
        # both ebx sharing one payload are listed (the old regex reader skipped repeats)
        self.assertEqual(sum(1 for n in names if "u_jersey_bears" in n), 2)
        self.assertNotIn("Screenshot0", names)


if __name__ == "__main__":
    unittest.main()
