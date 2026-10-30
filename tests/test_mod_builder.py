"""
tests/test_mod_builder.py: Unit tests for tools/mod_builder (docs/in_progress/MOD_BUILDER_PLAN.md §4): recipe
validation, the three recolour modes and the RGBA8 DDS writer, the asset index on a synthetic zstd cache, the
read-back check on synthetic .fbmods (helpers from tests/test_fbmod_inspect.py), install against a temp
manager_config.json and Mods folder with manager_running mocked, and the CLI (new, build --dry-run, build
through a fake harness). Everything lives in temp dirs; the real config, Mods folder and cache are never read.
"""
import io
import json
import os
import shutil
import struct
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from unittest import mock

import numpy as np
from PIL import Image

from headshots.portrait_converter.dds import decode_dds_header
from tests.test_fbmod_inspect import build_fbmod, chunk_fields, record
from tools import fbmod_inspect as fi
from tools.mod_builder import __main__ as cli
from tools.mod_builder import asset_index, install, mod_file_name, recolor
from tools.mod_builder import recipe as rc

FIRST_DOWN = "contentshared/football/playart/textures/linecolors/playartlinecolor_firstdownline"
JERSEY = "content/footballcharacter/items/uniform/jersey/jaguars/u_jersey_jaguars_2024_teal"
TEXTURE = 0x6BDE20BA


def good_recipe(**kw):
    data = {"slug": "yellow_first_down", "title": "Mu Yellow First Down Line", "author": "Tester",
            "version": "1.0", "category": "Cosmetic", "description": "yellow line", "route": "harness",
            "ops": [{"op": "texture-recolor", "asset": FIRST_DOWN,
                     "recolor": {"mode": "tint", "color": "#FFD400", "keep_alpha": True}}]}
    data.update(kw)
    return data


class TempDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mu_mod_builder_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        env = {"MU_MOD_BUILDS": os.path.join(self.tmp, "builds"),
               "MU_MMC_EDITOR_DIR": os.path.join(self.tmp, "editor"),
               "MMC_CONFIG": os.path.join(self.tmp, "MMC", "manager_config.json"),
               "MMC_MANAGER_DIR": os.path.join(self.tmp, "manager")}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)


# ---------------------------------------------------------------------------------------------- recipe ---
class RecipeTest(TempDirTest):
    def problems(self, **kw):
        return rc.validate(rc.from_dict(good_recipe(**kw)))

    def test_good_recipe_and_load(self):
        path = os.path.join(self.tmp, "recipe.json")
        data = good_recipe(ops=good_recipe()["ops"] + [
            {"op": "texture-replace", "asset": FIRST_DOWN, "file": "work/new.png", "route": "gui"},
            {"op": "ebx-set", "asset": JERSEY, "path": "Colors.Primary", "value": "#FF69B4"},
            {"op": "ebx-set", "asset": JERSEY, "path": "Count", "value": 3},
            {"op": "legacy-replace", "path": "common/database/json/x.json", "file": "work/x.json"},
            {"op": "texture-recolor", "asset": FIRST_DOWN, "recolor": {"mode": "hue-shift", "degrees": -30}},
            {"op": "texture-recolor", "asset": FIRST_DOWN,
             "recolor": {"mode": "map", "map": [["#FC6A29", "#FFD400", 40]]}}])
        with open(path, "w") as f:
            json.dump(data, f)
        r = rc.load_recipe(path)
        self.assertEqual(rc.validate(r), [])
        self.assertEqual([o.kind for o in r.ops][:5],
                         ["texture-recolor", "texture-replace", "ebx-set", "ebx-set", "legacy-replace"])
        self.assertEqual(r.ops[1].route, "gui")
        self.assertEqual(r.ops[0].route, "harness")
        self.assertEqual(r.ops[4].asset, "common/database/json/x.json")
        self.assertEqual(r.resolve("work/new.png"), os.path.join(self.tmp, "work", "new.png"))

    def test_unknown_op(self):
        p = self.problems(ops=[{"op": "mesh-import", "asset": FIRST_DOWN}])
        self.assertEqual(len(p), 1)
        self.assertIn("unknown op 'mesh-import'", p[0])

    def test_missing_asset(self):
        p = self.problems(ops=[{"op": "texture-recolor", "recolor": {"mode": "tint", "color": "#FFD400"}},
                               {"op": "legacy-replace", "file": "x.json"}])
        self.assertTrue(any("ops[0]" in x and "missing asset" in x for x in p), p)
        self.assertTrue(any("ops[1]" in x and "missing path" in x for x in p), p)

    def test_bad_colour(self):
        p = self.problems(ops=[{"op": "texture-recolor", "asset": FIRST_DOWN,
                                "recolor": {"mode": "tint", "color": "yellow"}}])
        self.assertTrue(any("#RRGGBB" in x for x in p), p)
        p = self.problems(ops=[{"op": "ebx-set", "asset": JERSEY, "path": "C", "value": "#FF69B"}])
        self.assertTrue(any("colour value" in x for x in p), p)
        p = self.problems(ops=[{"op": "texture-recolor", "asset": FIRST_DOWN,
                                "recolor": {"mode": "map", "map": [["#FC6A29", "red", 10]]}}])
        self.assertTrue(any("map[0]" in x for x in p), p)

    def test_bad_route(self):
        self.assertTrue(any("route 'robot'" in x for x in self.problems(route="robot")))
        ops = [dict(good_recipe()["ops"][0], route="hands")]
        self.assertTrue(any("ops[0]" in x and "route 'hands'" in x for x in self.problems(ops=ops)))

    def test_other_problems(self):
        self.assertTrue(any("no ops" in x for x in self.problems(ops=[])))
        self.assertTrue(any("missing title" in x for x in self.problems(title="")))
        self.assertTrue(any("slug" in x for x in self.problems(slug="Yellow Line")))
        p = self.problems(ops=[{"op": "texture-recolor", "asset": FIRST_DOWN, "recolor": {"mode": "blur"}},
                               {"op": "texture-replace", "asset": FIRST_DOWN, "file": "x.jpg"},
                               {"op": "ebx-set", "asset": JERSEY}])
        self.assertTrue(any("recolor mode 'blur'" in x for x in p), p)
        self.assertTrue(any(".png or .dds" in x for x in p), p)
        self.assertTrue(any("missing field 'path'" in x for x in p) and any("missing 'value'" in x for x in p), p)

    def test_mod_file_name(self):
        self.assertEqual(mod_file_name("yellow_first_down"), "Mu_YellowFirstDown.fbmod")


# --------------------------------------------------------------------------------------------- recolor ---
def swatch():
    """4×8 RGBA: an orange ramp (the first-down swatch is (252,106,41)), a grey pixel, varied alpha."""
    img = np.zeros((4, 8, 4), np.uint8)
    for x in range(8):
        k = (x + 1) / 8
        img[:, x, :3] = np.rint(np.array([252, 106, 41]) * k)
    img[0, 0, :3] = (128, 128, 128)
    img[..., 3] = np.arange(32, dtype=np.uint8).reshape(4, 8) * 8
    return img


def hsv(img):
    return recolor.rgb_to_hsv(img[..., :3].astype(float) / 255)


class RecolorTest(unittest.TestCase):
    def test_tint_sets_hue_keeps_value_and_alpha(self):
        src = swatch()
        out = recolor.recolor_array(src, {"mode": "tint", "color": "#FFD400"})
        h0, s0, v0 = hsv(src)
        h1, s1, v1 = hsv(out)
        coloured = s0 > 0.2
        np.testing.assert_allclose(h1[coloured], 50, atol=2.5)
        np.testing.assert_allclose(v1, v0, atol=1 / 255 + 1e-9)
        np.testing.assert_allclose(s1[coloured], s0[coloured], atol=0.03)
        np.testing.assert_array_equal(out[..., 3], src[..., 3])
        np.testing.assert_array_equal(out[0, 0, :3], (128, 128, 128))      # grey stays grey
        # the spike's swatch colour comes out yellow
        px = recolor.recolor_array(np.array([[[252, 106, 41, 255]]], np.uint8), {"mode": "tint", "color": "#FFD400"})
        r, g, b = (int(c) for c in px[0, 0, :3])
        self.assertEqual((r, b), (252, 41))
        self.assertTrue(212 <= g <= 218, g)

    def test_hue_shift(self):
        src = swatch()
        out = recolor.recolor_array(src, {"mode": "hue-shift", "degrees": 120})
        h0, _, v0 = hsv(src)
        h1, _, v1 = hsv(out)
        coloured = hsv(src)[1] > 0.2
        np.testing.assert_allclose((h1 - h0)[coloured] % 360, 120, atol=2.5)
        np.testing.assert_allclose(v1, v0, atol=1 / 255 + 1e-9)
        np.testing.assert_array_equal(out[..., 3], src[..., 3])
        back = recolor.recolor_array(src, {"mode": "hue-shift", "degrees": 360})
        self.assertLessEqual(np.abs(back.astype(int) - src.astype(int)).max(), 1)

    def test_map(self):
        src = swatch()
        out = recolor.recolor_array(src, {"mode": "map", "map": [["#FC6A29", "#FFD400", 40]]})
        hit = np.abs(src[..., :3].astype(int) - [252, 106, 41]).max(axis=-1) <= 40
        self.assertTrue(hit.any() and not hit.all())
        delta = np.array([255 - 252, 212 - 106, 0 - 41])
        expect = np.clip(src[..., :3].astype(int) + delta, 0, 255)
        np.testing.assert_array_equal(out[hit][:, :3], expect[hit])
        np.testing.assert_array_equal(out[~hit], src[~hit])
        np.testing.assert_array_equal(out[..., 3], src[..., 3])

    def test_keep_alpha_false_and_png_file(self):
        out = recolor.recolor_array(swatch(), {"mode": "tint", "color": "#FFD400", "keep_alpha": False})
        self.assertTrue((out[..., 3] == 255).all())
        with tempfile.TemporaryDirectory() as d:
            Image.fromarray(swatch(), "RGBA").save(os.path.join(d, "a.png"))
            recolor.recolor_png(os.path.join(d, "a.png"), os.path.join(d, "b.png"),
                                {"mode": "tint", "color": "#FFD400"})
            b = np.array(Image.open(os.path.join(d, "b.png")))
            np.testing.assert_array_equal(b, recolor.recolor_array(swatch(), {"mode": "tint", "color": "#FFD400"}))

    def test_png_to_dds_full_mip_chain(self):
        with tempfile.TemporaryDirectory() as d:
            img = np.zeros((4, 512, 4), np.uint8)
            img[..., 0], img[..., 1], img[..., 2], img[..., 3] = 252, 215, 41, 200
            img[:, :256, 3] = 100
            Image.fromarray(img, "RGBA").save(os.path.join(d, "t.png"))
            n = recolor.png_to_dds(os.path.join(d, "t.png"), os.path.join(d, "t.dds"))
            data = open(os.path.join(d, "t.dds"), "rb").read()
        self.assertEqual(n, 10)                                   # 512 -> 1, like the real swatch
        head = decode_dds_header(data)
        self.assertEqual((head["width"], head["height"], head["mip_count"]), (512, 4, 10))
        sizes = [max(1, 512 >> i) * max(1, 4 >> i) * 4 for i in range(10)]
        self.assertEqual(len(data), 128 + sum(sizes))
        self.assertEqual(data[128:132], bytes([41, 215, 252, 100]))   # BGRA
        last = data[-4:]
        self.assertEqual(last[:3], bytes([41, 215, 252]))
        self.assertEqual(last[3], 150)                            # box average of the two alpha halves


# ----------------------------------------------------------------------------------------- asset index ---
def cstr(s):
    return s.encode("latin-1") + b"\0"


def ebx_entry(name, etype, bundles=(0,), deps=0):
    return (cstr(name) + b"\x11" * 20 + struct.pack("<qqiB", 100, 200, 3, 0) + cstr(etype) + b"\x22" * 16
            + b"\x01" + b"\x00" * 18 + struct.pack("<i", len(bundles)) + b"".join(struct.pack("<I", b) for b in bundles)
            + struct.pack("<i", deps) + b"\x33" * 16 * deps)


def res_entry(name, rtype, bundles=(5,)):
    return (cstr(name) + b"\x44" * 20 + struct.pack("<qqiB", 111, 184, 3, 0) + struct.pack("<QIi", 7, rtype, 16)
            + b"\x0c" + b"\x00" * 15 + b"\x01" + b"\x00" * 18 + struct.pack("<i", len(bundles))
            + b"".join(struct.pack("<I", b) for b in bundles))


def write_cache(path, ebx, res, bundles=("win32/ui",), sbs=("win32/globals",), broken=False):
    import zstandard
    body = struct.pack("<5I", len(sbs), len(bundles), len(ebx), len(res), 0)
    body += b"".join(cstr(s) for s in sbs)
    body += b"".join(cstr(b) + b"\0\0\0\0" for b in bundles)
    body += struct.pack("<i", 1) + cstr("native_data/win32/cas_01.cas")
    entries = [ebx_entry(*e) for e in ebx]
    if broken:                      # a record that doesn't fit the layout (a huge bundle count)
        good = struct.pack("<iII", 2, 0, 3)
        assert good in entries[1]
        entries[1] = entries[1].replace(good, struct.pack("<iII", -7, 0, 3), 1)
    body += b"".join(entries) + b"".join(res_entry(*r) for r in res)
    head = b"FROSTY\x00\x02" + struct.pack("<I", 6) + b"\xe7\x15\x0c\x0d" + struct.pack("<I", 3468304) + b"\xab" * 20
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(head + zstandard.ZstdCompressor().compress(body))


EBX = [("ContentShared/football/PlayArt/textures/lineColors/PlayArtLineColor_FirstDownLine", "TextureAsset"),
       ("content/footballcharacter/items/uniform/jersey/jaguars/u_jersey_jaguars_2024_1gre_white", "Uniform", (0, 3), 2),
       ("Presentation/Banners/Screens/MondayNight/KickRangeBannerWidget_MN", "RimeWidgetBlueprint")]
RES = [(FIRST_DOWN, TEXTURE), ("presentation/director/bundling/cameradirector_brt", 0x428EC9D4),
       ("sound/thing_100%_nwa", 0x12345678)]


class AssetIndexTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.cache = os.path.join(self.tmp, "editor", "Caches", "madden27.cache")
        self.index = os.path.join(self.tmp, "index.sqlite")
        write_cache(self.cache, EBX, RES)

    def test_find_and_kinds(self):
        rows = asset_index.find("firstdownline", cache_path=self.cache, index_path=self.index)
        self.assertEqual({(r["kind"], r["type"]) for r in rows}, {("ebx", "TextureAsset"), ("res", "Texture")})
        self.assertIn(FIRST_DOWN, [r["name"] for r in rows])
        self.assertEqual([r["name"] for r in asset_index.find("FIRSTDOWN", kind="res", cache_path=self.cache,
                                                               index_path=self.index)], [FIRST_DOWN])
        brt = asset_index.find("cameradirector_brt", cache_path=self.cache, index_path=self.index)
        self.assertEqual(brt[0]["type"], "BundleRefTable")
        self.assertEqual(asset_index.find("100%", cache_path=self.cache, index_path=self.index)[0]["type"],
                         "0x12345678")
        self.assertEqual(asset_index.find("win32/ui", kind="bundle", cache_path=self.cache,
                                          index_path=self.index)[0]["name"], "win32/ui")
        self.assertEqual(asset_index.find("win32/globals", cache_path=self.cache, index_path=self.index), [])
        self.assertEqual(asset_index.find("100_", cache_path=self.cache, index_path=self.index), [])  # _ is literal

    def test_glob_and_limit(self):
        rows = asset_index.find("content*jaguars*", cache_path=self.cache, index_path=self.index)
        self.assertEqual([r["type"] for r in rows], ["Uniform"])
        self.assertEqual(len(asset_index.find("*firstdownline", cache_path=self.cache, index_path=self.index)), 2)
        self.assertEqual(len(asset_index.find("*firstdownline", cache_path=self.cache, index_path=self.index,
                                              limit=1)), 1)
        self.assertEqual(len(asset_index.find("/", cache_path=self.cache, index_path=self.index, limit=2)), 2)

    def test_default_paths_and_legacy(self):
        with open(asset_index.legacy_cache_path(self.cache), "wb") as f:
            f.write(b"\x00SFL" + struct.pack("<I", 2))
            for p in ("common/database/json/ingameconfig_defaults.json", "common/ml/cs_018.bin"):
                f.write(cstr(p) + b"\x55" * 40)
        self.assertEqual(asset_index.default_cache_path(), self.cache)
        rows = asset_index.find("ingameconfig")
        self.assertEqual(rows, [{"name": "common/database/json/ingameconfig_defaults.json", "kind": "legacy",
                                 "type": None}])
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "builds", "_index", "madden27_assets.sqlite")))

    def test_rebuild_only_when_cache_changes(self):
        asset_index.ensure_index(self.cache, self.index)
        with mock.patch.object(asset_index, "build_index", wraps=asset_index.build_index) as b:
            asset_index.ensure_index(self.cache, self.index)
            self.assertEqual(b.call_count, 0)
            write_cache(self.cache, EBX + [("content/new/thing", "Blueprint")], RES)
            st = os.stat(self.cache)
            os.utime(self.cache, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
            self.assertEqual(asset_index.find("new/thing", cache_path=self.cache, index_path=self.index)[0]["kind"],
                             "ebx")
            self.assertEqual(b.call_count, 1)

    def test_layout_mismatch_falls_back_to_heuristic(self):
        write_cache(self.cache, EBX, RES, broken=True)
        logs = []
        asset_index.ensure_index(self.cache, self.index, log=logs.append)
        self.assertTrue(any("layout not recognised" in m for m in logs), logs)
        rows = asset_index.find("firstdownline", cache_path=self.cache, index_path=self.index)
        self.assertIn(("ebx", "PlayArtLineColor_FirstDownLine"), {(r["kind"], r["name"].rsplit("/", 1)[-1]) for r in rows})
        self.assertIn((FIRST_DOWN, "unknown"), {(r["name"], r["kind"]) for r in rows})

    def test_bounded_reader_crosses_chunks(self):
        with mock.patch.object(asset_index, "CHUNK", 7):
            rows = list(asset_index.iter_cache(self.cache))
        self.assertEqual(len(rows), 1 + len(EBX) + len(RES))

    def test_not_a_cache(self):
        with open(self.cache, "wb") as f:
            f.write(b"nope" * 20)
        with self.assertRaises(ValueError):
            asset_index.build_index(self.cache, self.index)


# ------------------------------------------------------------------------------------------ check/install ---
ENC = fi.ENCRYPTED_MAGIC


def yellow_mod(title="Mu Yellow First Down Line", extra=False, plain=False, missing=False, author=None):
    payloads = [ENC + b"\0" * 56, ENC + b"\1" * 56, (b"plain" if plain else ENC) + b"\2" * 56]
    records = [record(0, 0, "Icon", size=64), record(0, -1, "Screenshot0")]
    if not missing:
        records.append(record(2, 1, FIRST_DOWN, size=184) + struct.pack("<IQi", TEXTURE, 1, 16) + b"\x0c" + b"\0" * 15)
    records.append(record(3, 2, "2fdfc2a6-bc8c-1211-0569-d9ea61f087d5")
                   + chunk_fields(64, 2048, fi.fnv1(FIRST_DOWN), extra=(26,)))
    if extra:
        records.append(record(1, 1, "content/ui/widgets/perfoverlay/perfoverlayvramwarning", size=99))
    buf = build_fbmod(payloads, records, title=title)
    if author:
        buf = buf.replace(b"Tester\0", author.encode() + b"\0", 1)
        # build_fbmod's data offset is fixed before the swap: rebuild the header for the new length
        delta = len(author) - len("Tester")
        off, n = struct.unpack_from("<qi", buf, 12)
        buf = buf[:12] + struct.pack("<qi", off + delta, n) + buf[24:]
    return buf


class CheckTest(TempDirTest):
    def check(self, buf, **kw):
        path = os.path.join(self.tmp, "m.fbmod")
        with open(path, "wb") as f:
            f.write(buf)
        return install.check(path, rc.from_dict(good_recipe(**kw)))

    def test_good_build(self):
        r = self.check(yellow_mod())
        self.assertTrue(r["ok"], r)
        self.assertEqual((r["missing"], r["extra"], r["unencrypted"], r["details_mismatch"]), ([], [], [], {}))
        self.assertEqual(r["game_version"], 3468304)

    def test_missing_asset(self):
        r = self.check(yellow_mod(missing=True))
        self.assertFalse(r["ok"])
        self.assertEqual(r["missing"], [FIRST_DOWN])
        self.assertEqual(r["extra"], ["2fdfc2a6-bc8c-1211-0569-d9ea61f087d5"])   # orphaned chunk

    def test_extra_asset(self):
        r = self.check(yellow_mod(extra=True))
        self.assertFalse(r["ok"])
        self.assertEqual(r["extra"], ["content/ui/widgets/perfoverlay/perfoverlayvramwarning"])

    def test_wrong_title_and_author(self):
        r = self.check(yellow_mod(title="Someone Else's Mod", author="Nobody"))
        self.assertFalse(r["ok"])
        self.assertEqual(r["details_mismatch"]["title"], {"recipe": "Mu Yellow First Down Line",
                                                          "mod": "Someone Else's Mod"})
        self.assertEqual(r["details_mismatch"]["author"]["mod"], "Nobody")

    def test_unencrypted_payload(self):
        r = self.check(yellow_mod(plain=True))
        self.assertFalse(r["ok"])
        self.assertEqual(r["unencrypted"], ["2fdfc2a6-bc8c-1211-0569-d9ea61f087d5"])

    def test_legacy_op(self):
        path = "common/database/json/ingameconfig_defaults.json"
        payloads = [ENC + b"\0" * 40, ENC + b"\1" * 40]
        records = [record(3, 0, "383d5f85-1843-a8a8-1167-4a724f97c001", user_data="legacy;" + path)
                   + chunk_fields(0, 18, fi.fnv1(path), first_mip=-1, extra=(9,)),
                   record(3, 1, "12723b33-68df-97d8-a361-9f810e933e61", size=52, handler=0xBD9BFB65,
                          user_data="legacy;Collector (ContentShared/global/RtamStorages/Gamemode_Common)")
                   + chunk_fields(0, 0, 0, handler=0xBD9BFB65)]
        buf = build_fbmod(payloads, records, title="Mu Yellow First Down Line")
        r = self.check(buf, ops=[{"op": "legacy-replace", "path": path, "file": "x.json"}])
        self.assertTrue(r["ok"], r)


BASE = [("MUTRemover1.6.fbmod", False), ("No VRAM Warning.fbmod", True), ("Mu_YellowFirstDown.fbmod", False),
        ("M27 yellow first down marker.fbmod", True)]


class InstallTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.cfg = os.environ["MMC_CONFIG"]
        self.mods = os.path.join(os.environ["MMC_MANAGER_DIR"], "Mods", "Madden27")
        os.makedirs(os.path.dirname(self.cfg))
        os.makedirs(self.mods)
        self.original = {"Games": {"Madden27": {"Packs": {"Mu Base": "|".join(f"{n}:{e}" for n, e in BASE),
                                                          "Default": "a.fbmod:True"},
                                                "Options": {"SelectedPack": "Default"}}}, "Other": 1}
        with open(self.cfg, "w") as f:
            json.dump(self.original, f)
        self.src = os.path.join(self.tmp, "yellow_first_down.fbmod")
        with open(self.src, "wb") as f:
            f.write(yellow_mod())
        with open(os.path.join(self.mods, "BCA Pink Oct.fbmod"), "wb") as f:
            f.write(b"not ours")
        running = mock.patch("pipeline.mod_packs.manager_running", return_value=False)
        self.running = running.start()
        self.addCleanup(running.stop)

    def config(self):
        with open(self.cfg) as f:
            return json.load(f)

    def test_installs_and_writes_pack_in_order(self):
        out = install.install(self.src)
        self.assertEqual(out["mod"], os.path.join(self.mods, "Mu_YellowFirstDown.fbmod"))
        self.assertEqual(open(out["mod"], "rb").read(), yellow_mod())
        game = self.config()["Games"]["Madden27"]
        self.assertEqual(game["Options"]["SelectedPack"], "Mu Test")
        self.assertEqual(game["Packs"]["Mu Test"],
                         "MUTRemover1.6.fbmod:False|No VRAM Warning.fbmod:True|"
                         "M27 yellow first down marker.fbmod:True|Mu_YellowFirstDown.fbmod:True")
        self.assertEqual(game["Packs"]["Mu Base"], self.original["Games"]["Madden27"]["Packs"]["Mu Base"])
        self.assertEqual(self.config()["Other"], 1)
        # the backup is the config as it was before the write
        self.assertTrue(os.path.basename(out["backup"]).startswith("manager_config.backup-"))
        with open(out["backup"]) as f:
            self.assertEqual(json.load(f), self.original)
        self.assertEqual(open(os.path.join(self.mods, "BCA Pink Oct.fbmod"), "rb").read(), b"not ours")

    def test_reinstall_replaces_our_file_and_makes_new_backup(self):
        first = install.install(self.src, pack="Mu Try")
        second = install.install(self.src, pack="Mu Try")
        self.assertFalse(first["replaced"])
        self.assertTrue(second["replaced"])
        self.assertNotEqual(first["backup"], second["backup"])
        self.assertEqual(self.config()["Games"]["Madden27"]["Packs"]["Mu Try"].count("Mu_YellowFirstDown"), 1)

    def test_refuses_while_manager_running(self):
        self.running.return_value = True
        with self.assertRaises(install.InstallError):
            install.install(self.src)
        self.assertEqual(self.config(), self.original)
        self.assertFalse(os.path.exists(os.path.join(self.mods, "Mu_YellowFirstDown.fbmod")))
        self.assertEqual([f for f in os.listdir(os.path.dirname(self.cfg))], ["manager_config.json"])

    def test_never_overwrites_a_mod_that_isnt_ours(self):
        for name in ("BCA Pink Oct.fbmod", "../Mu_x.fbmod", "Mu_x.txt"):
            with self.assertRaises(install.InstallError):
                install.install(self.src, name=name)
        self.assertEqual(open(os.path.join(self.mods, "BCA Pink Oct.fbmod"), "rb").read(), b"not ours")
        self.assertEqual(self.config(), self.original)
        named = os.path.join(self.tmp, "BCA Pink Oct.fbmod")
        shutil.copy(self.src, named)
        out = install.install(named)                          # a non-Mu_ source is renamed, not copied over
        self.assertEqual(os.path.basename(out["mod"]), "Mu_BCA Pink Oct.fbmod")
        self.assertEqual(open(os.path.join(self.mods, "BCA Pink Oct.fbmod"), "rb").read(), b"not ours")

    def test_refuses_to_write_mu_base(self):
        with self.assertRaises(install.InstallError):
            install.install(self.src, pack="Mu Base")
        self.assertEqual(self.config(), self.original)


# ------------------------------------------------------------------------------------------------- CLI ---
class CliTest(TempDirTest):
    def run_cli(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                rc_ = cli.main(list(argv))
            except SystemExit as e:
                rc_ = e.code if isinstance(e.code, int) else (print(e.code) or 1)
        return rc_, buf.getvalue()

    def new_recipe(self, ops=None):
        self.assertEqual(self.run_cli("new", "yellow_first_down")[0], 0)
        self.folder = os.path.join(os.environ["MU_MOD_BUILDS"], "yellow_first_down")
        path = os.path.join(self.folder, "recipe.json")
        data = json.load(open(path))
        self.assertEqual(data["ops"], [])
        data.update(good_recipe(), ops=ops or good_recipe()["ops"])
        with open(path, "w") as f:
            json.dump(data, f)

    def test_new_refuses_twice_and_empty_recipe_fails(self):
        self.run_cli("new", "yellow_first_down")
        code, out = self.run_cli("new", "yellow_first_down")
        self.assertEqual(code, 1)
        code, out = self.run_cli("build", "yellow_first_down", "--dry-run")
        self.assertEqual(code, 1)
        self.assertIn("no ops", out)

    def test_dry_run_prints_harness_commands(self):
        self.new_recipe(good_recipe()["ops"] + [
            {"op": "ebx-set", "asset": JERSEY, "path": "Colors.Primary", "value": "#FF69B4"}])
        code, out = self.run_cli("build", "yellow_first_down", "--dry-run")
        self.assertEqual(code, 0)
        lines = [l.strip() for l in out.splitlines()[1:]]
        cmds = [l.split()[2] for l in lines if l.startswith("harness:")]
        self.assertEqual(cmds, ["info", "export-texture", "import-texture", "ebx-set", "export-mod"])
        self.assertTrue(any(l.startswith("local:") and "recolor" in l for l in lines))
        self.assertIn('--title "Mu Yellow First Down Line"', out)
        self.assertFalse(os.path.exists(os.path.join(self.folder, "build.log")))

    def test_gui_route_prints_steps_and_recolours_exported_png(self):
        self.new_recipe()
        code, out = self.run_cli("build", "yellow_first_down", "--route", "gui")
        self.assertEqual(code, 0)
        self.assertIn("Export Mod", out)
        self.assertNotIn("MmcHarness", out)
        png = os.path.join(self.folder, "work", "playartlinecolor_firstdownline.png")
        Image.fromarray(swatch(), "RGBA").save(png)
        code, out = self.run_cli("build", "yellow_first_down", "--route", "gui")
        self.assertIn("done here", out)
        self.assertTrue(os.path.exists(png.replace(".png", "_recolor.png")))

    def test_build_without_harness(self):
        self.new_recipe()
        with mock.patch.object(cli, "_load_harness", return_value=None):
            code, out = self.run_cli("build", "yellow_first_down")
        self.assertEqual(code, 2)
        self.assertIn("harness isn't installed yet", out)

    def test_harness_import_is_lazy(self):
        # tools/mod_builder/harness.py belongs to another unit; when it isn't there, _load_harness says so
        with mock.patch("importlib.import_module", side_effect=ModuleNotFoundError(
                "no", name="tools.mod_builder.harness")):
            self.assertIsNone(cli._load_harness())

    def test_build_through_fake_harness_then_check_and_install(self):
        self.new_recipe()
        calls = []

        class HarnessError(Exception):
            pass

        def run(cmd, timeout=600):
            calls.append(cmd)
            if cmd[0] == "export-texture":
                Image.fromarray(swatch(), "RGBA").save(cmd[2])
            if cmd[0] == "export-mod":
                with open(cmd[2], "wb") as f:
                    f.write(yellow_mod())
            return {"ok": True}

        fake = types.SimpleNamespace(run=run, HarnessError=HarnessError)
        with mock.patch.object(cli, "_load_harness", return_value=fake):
            code, out = self.run_cli("build", "yellow_first_down")
        self.assertEqual(code, 0, out)
        self.assertEqual([c[0] for c in calls], ["info", "export-texture", "import-texture", "export-mod"])
        dds = calls[2][3]
        self.assertTrue(dds.endswith("_recolor.dds") and os.path.exists(dds))
        log = open(os.path.join(self.folder, "build.log")).read()
        self.assertIn("export-mod", log)
        check = json.load(open(os.path.join(self.folder, "check.json")))
        self.assertTrue(check["ok"])

        cfg = os.environ["MMC_CONFIG"]
        os.makedirs(os.path.dirname(cfg))
        os.makedirs(os.path.join(os.environ["MMC_MANAGER_DIR"], "Mods", "Madden27"))
        with open(cfg, "w") as f:
            json.dump({"Games": {"Madden27": {"Packs": {"Mu Base": "a.fbmod:True"}}}}, f)
        with mock.patch("pipeline.mod_packs.manager_running", return_value=False):
            code, out = self.run_cli("install", "yellow_first_down", "--pack", "Mu Test")
        self.assertEqual(code, 0, out)
        self.assertEqual(json.load(open(cfg))["Games"]["Madden27"]["Packs"]["Mu Test"],
                         "a.fbmod:True|Mu_YellowFirstDown.fbmod:True")

    def test_harness_error_stops_build(self):
        self.new_recipe()

        class HarnessError(Exception):
            pass

        def run(cmd, timeout=600):
            raise HarnessError("profile failed to load")

        with mock.patch.object(cli, "_load_harness",
                               return_value=types.SimpleNamespace(run=run, HarnessError=HarnessError)):
            code, out = self.run_cli("build", "yellow_first_down")
        self.assertEqual(code, 1)
        self.assertIn("profile failed to load", open(os.path.join(self.folder, "build.log")).read())

    def test_find_cli(self):
        write_cache(os.path.join(os.environ["MU_MMC_EDITOR_DIR"], "Caches", "madden27.cache"), EBX, RES)
        code, out = self.run_cli("find", "firstdownline")
        self.assertEqual(code, 0)
        self.assertIn(FIRST_DOWN, out)
        code, out = self.run_cli("find", "nothing_like_this")
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
