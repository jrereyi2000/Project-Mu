"""Offline tests for coach headshots (COACH_SAVE_WRITES_PLAN §2.7 / U4).

Covered:
- coach mode never puts "jersey" in the prompt and does put it in the negative;
- the same seed gives the same coach appearance;
- the player default is byte-identical (golden appearance line);
- coach tone group and body type feed the draw;
- MockBackend produces both the WebP (700x508) and DDS (512 BC3) outputs;
- a coach that already has a headshot is skipped;
- the coach donor tool registers plates with aperture masks.

No network, no database, no GPU, no model download.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image

import headshots.coach_headshots as ch
from headshots.headshot_appearance_generator import (
    COACH_FACIAL,
    COACH_GLASSES_CHANCE,
    appearance,
    coach_age_cue,
)
from tools import build_coach_donor_library as donor_tool

# The player appearance line before coach mode existed. This exact string is the
# byte-identical guarantee for the default (player) draw.
GOLDEN_PLAYER_LINE = (
    "Jake Miller | Patriots | WR | skin tone: medium-deep brown | hair: "
    "medium-length wavy hair swept back (black hair) | facial hair: clean-shaven | "
    "athletic build, moderately thick neck, broad shoulders | mid-20s | face: an "
    "average-looking man, approachable, not model-like; mildly asymmetric, natural "
    "face, ordinary jawline, average nose, clear, even skin, average eyes | "
    "attr_score: 5.9"
)


def _profile(**overrides):
    profile = {
        "coach_key": "coach-1",
        "person_kind": "COACH",
        "person_id": 1,
        "first_name": "Bill",
        "last_name": "Doe",
        "age": 58,
        "position": "HeadCoach",
        "height": 74,
        "weight": 220,
        "body_type": "Heavy",
        "face_shape": "coachhead_6_B_N_20",
    }
    profile.update(overrides)
    return profile


def _synthetic_coach_plate(path: Path, fabric=(40, 90, 150), w=896, h=1200) -> None:
    """A plain coaching-top photo: skin neck above a solid fabric torso."""
    arr = np.zeros((h, w, 4), dtype=np.uint8)
    arr[:, :, :] = [255, 255, 255, 255]
    arr[: int(h * 0.63), int(w * 0.36):int(w * 0.64)] = [200, 150, 120, 255]
    arr[int(h * 0.63):, int(w * 0.08):int(w * 0.92)] = [*fabric, 255]
    Image.fromarray(arr).save(path)


class TestCoachAppearance(unittest.TestCase):
    def test_player_default_is_unchanged(self):
        line, meta = appearance("Jake Miller", "Patriots", "WR", 73, 205.0, 24)
        self.assertEqual(line, GOLDEN_PLAYER_LINE)
        self.assertEqual(meta, {
            "pos": "WR", "tone": "medium-deep brown",
            "hair": "medium-length wavy hair swept back (black hair)",
            "facial": "clean-shaven", "score": 5.9,
        })

    def test_explicit_player_role_matches_default(self):
        default_line, _ = appearance("Jake Miller", "Patriots", "WR", 73, 205.0, 24)
        player_line, _ = appearance(
            "Jake Miller", "Patriots", "WR", 73, 205.0, 24, role="player",
            skin_tone_group=7, body_type="Heavy",
        )
        self.assertEqual(default_line, player_line)

    def test_same_seed_same_coach_appearance(self):
        first = appearance("Bill Doe", "Panthers", "Head Coach", 74, 220.0, 58, role="coach")
        second = appearance("Bill Doe", "Panthers", "Head Coach", 74, 220.0, 58, role="coach")
        self.assertEqual(first, second)
        self.assertEqual(first[0], second[0])
        self.assertEqual(first[1], second[1])

    def test_coach_skin_tone_group_feeds_draw(self):
        light, light_meta = appearance(
            "Bill Doe", "Panthers", "Head Coach", 74, 220.0, 58,
            role="coach", skin_tone_group=1)
        deep, deep_meta = appearance(
            "Bill Doe", "Panthers", "Head Coach", 74, 220.0, 58,
            role="coach", skin_tone_group=7)
        self.assertEqual(light_meta["tone"], "light")
        self.assertEqual(deep_meta["tone"], "deep brown")
        self.assertNotEqual(light, deep)
        # The face preset's first digit is the skin-tone group (MADDEN_COACH_RECORDS §3.12).
        preset, preset_meta = appearance(
            "Bill Doe", "Panthers", "Head Coach", 74, 220.0, 58,
            role="coach", skin_tone_group="coachhead_1_H_N_01")
        self.assertEqual(preset_meta["tone"], "light")
        self.assertEqual(preset, light)

    def test_coach_body_type_feeds_build(self):
        _thin_line, thin = appearance(
            "Bill Doe", "Panthers", "Head Coach", 76, 200.0, 45,
            role="coach", body_type="Thin")
        _heavy_line, heavy = appearance(
            "Bill Doe", "Panthers", "Head Coach", 76, 200.0, 45,
            role="coach", body_type="Heavy")
        self.assertIn("lean build", thin["build"])
        self.assertIn("very large build", heavy["build"])

    def test_coach_build_comes_from_height_and_weight(self):
        _small_line, small = appearance(
            "Bill Doe", "Panthers", "Head Coach", 70, 160.0, 45,
            role="coach", body_type="Standard")
        _large_line, large = appearance(
            "Bill Doe", "Panthers", "Head Coach", 78, 330.0, 45,
            role="coach", body_type="Standard")
        self.assertIn("lean build", small["build"])
        self.assertIn("very large build", large["build"])

    def test_coach_age_cue_runs_to_72(self):
        young = coach_age_cue(33)
        old = coach_age_cue(70)
        self.assertIn("30s", young)
        self.assertTrue("60s" in old or "70s" in old)
        self.assertIn("grey", old.lower())
        # Clamped to the 32-72 coach range.
        self.assertEqual(coach_age_cue(20), coach_age_cue(32))
        self.assertEqual(coach_age_cue(90), coach_age_cue(72))

    def test_coach_glasses_and_facial_hair_constants(self):
        self.assertAlmostEqual(COACH_GLASSES_CHANCE, 0.25)
        weights = dict(COACH_FACIAL)
        self.assertEqual(sum(weights.values()), 100)
        self.assertEqual(weights["clean-shaven"], 60)  # ~40% wear facial hair


class TestCoachPrompt(unittest.TestCase):
    def test_no_jersey_in_positive_and_negative_has_it(self):
        prompt = ch.build_coach_prompt(_profile(), "Panthers")
        self.assertNotIn("jersey", prompt.lower())
        self.assertIn("jersey", ch.COACH_NEGATIVE_PROMPT.lower())
        for term in ("shoulder pads", "helmet", "number"):
            self.assertIn(term, ch.COACH_NEGATIVE_PROMPT.lower())

    def test_prompt_is_coaching_clothes(self):
        prompt = ch.build_coach_prompt(_profile(), "Panthers")
        lowered = prompt.lower()
        self.assertIn("coach", lowered)
        self.assertIn("polo", lowered)
        self.assertIn("quarter-zip", lowered)
        # Team colours come from the shared player palette.
        self.assertIn("#0085CA", prompt)  # Carolina Process Blue

    def test_generate_passes_coach_negative_prompt_to_backend(self):
        captured = {}

        def fake_generate_headshot(**kwargs):
            captured.update(kwargs)
            from headshots.local.backends import MockBackend
            return MockBackend().generate(kwargs["prompt"], size=256)

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(ch, "generate_headshot", side_effect=fake_generate_headshot), \
                 mock.patch.object(ch, "_remove_background", side_effect=lambda im: im.convert("RGBA")):
                ch.generate_coach_headshot(
                    _profile(), "Panthers", static_dir=tmp, dds_dir=tmp,
                    donor_dir=tmp, backend="mock")
        self.assertNotIn("jersey", captured["prompt"].lower())
        self.assertIn("jersey", captured["config"]["negative_prompt"].lower())


class TestCoachHeadshotsMock(unittest.TestCase):
    def _donor_dir(self, tmp: Path) -> Path:
        donor_dir = tmp / "coach_donors"
        donor_dir.mkdir()
        plate_path = donor_dir / "CAR_1.png"
        _synthetic_coach_plate(plate_path)
        plate = donor_tool.save_plate(donor_dir, "CAR", 1, Image.open(plate_path),
                                      collar_kind="polo")
        registry = {}
        donor_tool.register_plate(registry, "CAR", plate)
        donor_tool.save_registry(registry, donor_dir / "registry.json")
        return donor_dir

    def test_mock_backend_produces_webp_and_dds(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            donor_dir = self._donor_dir(tmp)
            static_dir = tmp / "static" / "coaches"
            dds_dir = tmp / "data" / "headshots" / "dds" / "coaches"
            with mock.patch.object(ch, "_remove_background",
                                   side_effect=lambda im: im.convert("RGBA")):
                webp_path, dds_path = ch.generate_coach_headshot(
                    _profile(), "Panthers",
                    static_dir=str(static_dir), dds_dir=str(dds_dir),
                    donor_dir=str(donor_dir), backend="mock")

            self.assertTrue(os.path.exists(webp_path))
            self.assertTrue(os.path.exists(dds_path))
            self.assertEqual(Image.open(webp_path).size, (700, 508))
            self.assertEqual(Path(webp_path).name, "coach-1.webp")
            self.assertEqual(Path(dds_path).name, "coach-1.dds")
            with open(dds_path, "rb") as f:
                self.assertEqual(f.read(4), b"DDS ")

    def test_existing_headshot_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            static_dir = tmp / "static"
            dds_dir = tmp / "dds"
            static_dir.mkdir()
            existing = static_dir / "coach-1.webp"
            existing.write_bytes(b"already-here")

            # A backend that does not exist proves generation was never attempted.
            webp_path, _dds_path = ch.generate_coach_headshot(
                _profile(), "Panthers", static_dir=str(static_dir),
                dds_dir=str(dds_dir), backend="does-not-exist",
                skip_existing=True)

            self.assertEqual(webp_path, str(existing))
            self.assertEqual(existing.read_bytes(), b"already-here")

    def test_batch_skips_coaches_with_a_headshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            static_dir = Path(tmp)
            (static_dir / "coach-1.webp").write_bytes(b"x")
            profiles = [_profile(), _profile(coach_key="coach-2", person_id=2)]
            needing = ch.coaches_needing_headshots(profiles, static_dir=str(static_dir))
            self.assertEqual([p["coach_key"] for p in needing], ["coach-2"])


class TestCoachDonorLibrary(unittest.TestCase):
    def test_strip_transform_returns_raw_upload(self):
        thumb = ("https://static.clubs.nfl.com/image/upload/t_headshot_desktop/"
                 "f_auto/lions/bxt18sbncaorsj4s6g9c.jpg")
        self.assertEqual(
            donor_tool.strip_transform(thumb),
            "https://static.clubs.nfl.com/image/upload/f_auto/lions/bxt18sbncaorsj4s6g9c.jpg")
        private = ("https://static.clubs.nfl.com/image/private/t_lazy/"
                   "t_person_squared_mobile/f_png/patriots/abc.png")
        self.assertEqual(
            donor_tool.strip_transform(private),
            "https://static.clubs.nfl.com/image/private/f_png/patriots/abc.png")

    def test_extract_handles_escaped_and_scheme_relative_urls(self):
        html = (
            '{"a": "https:\\/\\/static.clubs.nfl.com\\/image\\/upload\\/t_lazy\\/'
            'f_png\\/patriots\\/abc.png"} '
            'src="//static.clubs.nfl.com/image/private/f_auto/eagles/xyz.jpg" '
            'src="https://static.clubs.nfl.com/image/upload/f_auto/lions/abc.jpg"'
        )
        urls = donor_tool.extract_photo_urls(html)
        self.assertIn("https://static.clubs.nfl.com/image/upload/f_png/patriots/abc.png", urls)
        self.assertIn("https://static.clubs.nfl.com/image/private/f_auto/eagles/xyz.jpg", urls)
        self.assertIn("https://static.clubs.nfl.com/image/upload/f_auto/lions/abc.jpg", urls)
        self.assertEqual(len(urls), len(set(urls)))

    def test_registers_plates_with_masks(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            image = Image.new("RGBA", (1200, 675), (40, 90, 150, 255))
            plate = donor_tool.save_plate(out_dir, "CAR", 1, image,
                                          collar_kind="quarter_zip")
            registry = {}
            donor_tool.register_plate(registry, "car", plate)
            registry_path = donor_tool.save_registry(registry, out_dir / "registry.json")

            data = json.loads(registry_path.read_text())
            self.assertIn("CAR", data)
            self.assertEqual(len(data["CAR"]["plates"]), 1)
            saved = data["CAR"]["plates"][0]
            self.assertEqual(saved["id"], "CAR_1")
            self.assertEqual(saved["collar_kind"], "quarter_zip")
            self.assertEqual(saved["mask_polygon"], donor_tool.mask_polygon_for("quarter_zip"))
            self.assertTrue(os.path.exists(out_dir / "CAR_1.png"))
            self.assertTrue(os.path.exists(out_dir / "CAR_1_mask.png"))

            mask = Image.open(out_dir / "CAR_1_mask.png")
            self.assertEqual(mask.size, (1024, 1024))
            self.assertEqual(mask.mode, "L")
            self.assertEqual(mask.getpixel((512, 500)), 255)   # repaint zone
            self.assertEqual(mask.getpixel((10, 1010)), 0)     # preserved fabric

    def test_mask_polygons_never_use_jersey_tiers(self):
        from headshots.local.donors import POLYS_5TIER

        coach_polys = {tuple(map(tuple, donor_tool.mask_polygon_for(k)))
                       for k in donor_tool.COLLAR_KINDS}
        for poly in POLYS_5TIER.values():
            self.assertNotIn(tuple(poly), coach_polys)


if __name__ == "__main__":
    unittest.main()
