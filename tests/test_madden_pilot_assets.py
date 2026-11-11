"""Asset-check tests (MADDEN_SPIKE_AGENT_PLAN section 4) on synthetic images.

Pillow draws the frames; no live frame and no database are touched.
"""
import json

from PIL import Image
import pytest

from tools.madden_pilot import assets
from tools.madden_pilot.checks import asset_probe

CYAN = (0, 255, 255)
GREY = (128, 128, 128)
CYAN_HUE = (170.0, 200.0)


def make(path, colour=CYAN, size=(80, 80)):
    Image.new("RGB", size, colour).save(path)
    return path


def half_cyan_half_grey(path):
    image = Image.new("RGB", (80, 80), CYAN)
    image.paste(Image.new("RGB", (40, 80), GREY), (40, 0))
    image.save(path)
    return path


# --------------------------------------------------------------------------- colour_share
def test_colour_share_pure_cyan_is_one(tmp_path):
    frame = make(tmp_path / "cyan.png")
    assert assets.colour_share(frame, (0.0, 0.0, 1.0, 1.0), CYAN_HUE) == pytest.approx(1.0)


def test_colour_share_grey_is_zero(tmp_path):
    frame = make(tmp_path / "grey.png", GREY)
    assert assets.colour_share(frame, (0.0, 0.0, 1.0, 1.0), CYAN_HUE) == pytest.approx(0.0)


def test_colour_share_reads_only_the_region(tmp_path):
    frame = half_cyan_half_grey(tmp_path / "half.png")
    left = assets.colour_share(frame, (0.0, 0.0, 0.5, 1.0), CYAN_HUE)
    right = assets.colour_share(frame, (0.5, 0.0, 0.5, 1.0), CYAN_HUE)
    assert left == pytest.approx(1.0)
    assert right == pytest.approx(0.0)
    assert assets.colour_share(frame, (0.0, 0.0, 1.0, 1.0), CYAN_HUE) == pytest.approx(0.5, abs=0.05)


def test_colour_share_empty_region_is_zero(tmp_path):
    frame = make(tmp_path / "cyan.png")
    assert assets.colour_share(frame, (0.6, 0.6, 0.0, 0.0), CYAN_HUE) == 0.0


# --------------------------------------------------------------------------- crop_similarity
def test_crop_similarity_identical_is_one(tmp_path):
    frame = make(tmp_path / "cyan.png")
    assert assets.crop_similarity(frame, (0.0, 0.0, 1.0, 1.0), frame) == pytest.approx(1.0)


def test_crop_similarity_noise_is_low(tmp_path):
    frame = make(tmp_path / "cyan.png")
    noise = tmp_path / "noise.png"
    Image.effect_noise((80, 80), 120).convert("RGB").save(noise)
    score = assets.crop_similarity(frame, (0.0, 0.0, 1.0, 1.0), noise)
    assert 0.0 <= score < 0.6


# --------------------------------------------------------------------------- check_frame
def _reference_spec(pass_png, fail_png, margin=0.05):
    return {"reference": {"pass": str(pass_png), "fail": str(fail_png), "margin": margin}}


def test_check_frame_passes_when_every_measure_passes(tmp_path):
    frame = make(tmp_path / "cyan.png")
    grey = make(tmp_path / "grey.png", GREY)
    spec = {"name": "cyan", "region": [0.0, 0.0, 1.0, 1.0],
            "colour": {"hue": list(CYAN_HUE), "pass_at": 0.02},
            "reference": {"pass": str(frame), "fail": str(grey), "margin": 0.05}}
    result = assets.check_frame(frame, spec)
    assert result["status"] == "pass"
    assert result["measures"]["colour_share"] == pytest.approx(1.0)
    assert result["measures"]["similarity_pass"] > result["measures"]["similarity_fail"]
    assert "colour share" in result["why"]


def test_check_frame_fails_when_every_measure_points_the_other_way(tmp_path):
    frame = make(tmp_path / "grey.png", GREY)
    cyan = make(tmp_path / "cyan.png")
    spec = {"name": "cyan", "region": [0.0, 0.0, 1.0, 1.0],
            "colour": {"hue": list(CYAN_HUE), "pass_at": 0.02},
            "reference": {"pass": str(cyan), "fail": str(frame), "margin": 0.05}}
    result = assets.check_frame(frame, spec)
    assert result["status"] == "fail"
    assert result["measures"]["similarity_fail"] > result["measures"]["similarity_pass"]


def test_check_frame_is_unknown_with_mixed_measures(tmp_path):
    frame = half_cyan_half_grey(tmp_path / "half.png")
    grey = make(tmp_path / "grey.png", GREY)
    cyan = make(tmp_path / "cyan.png")
    spec = {"name": "mixed", "region": [0.0, 0.0, 1.0, 1.0],
            "colour": {"hue": list(CYAN_HUE), "pass_at": 0.4},
            # A 50/50 frame is close to both references, so the reference is inconclusive.
            "reference": {"pass": str(grey), "fail": str(cyan), "margin": 0.9}}
    result = assets.check_frame(frame, spec)
    assert result["status"] == "unknown"
    assert "inconclusive" in result["why"]


def test_check_frame_unknown_without_measures(tmp_path):
    frame = make(tmp_path / "cyan.png")
    assert assets.check_frame(frame, {})["status"] == "unknown"


def test_check_frame_unknown_on_missing_reference(tmp_path):
    frame = make(tmp_path / "cyan.png")
    spec = {"reference": {"pass": str(tmp_path / "nope.png"),
                          "fail": str(tmp_path / "also-nope.png"), "margin": 0.05}}
    result = assets.check_frame(frame, spec)
    assert result["status"] == "unknown"
    assert "could not read" in result["why"]


# --------------------------------------------------------------------------- asset_probe
class FakePilot:
    def __init__(self, png=None):
        self.last_png = png
        self.looked = False

    def look(self):
        self.looked = True
        if self.last_png is None:
            raise AssertionError("test pilot has no frame")


def write_spec(path, spec):
    path.write_text(json.dumps(spec), encoding="utf-8")
    return path


def cyan_spec(path):
    return write_spec(path, {"name": "cyan", "region": [0.0, 0.0, 1.0, 1.0],
                             "colour": {"hue": list(CYAN_HUE), "pass_at": 0.02}})


def test_asset_probe_without_env_is_unknown(tmp_path, monkeypatch):
    monkeypatch.delenv(asset_probe.ENV_ASSET_PROBE, raising=False)
    result = asset_probe.run(FakePilot())
    assert result["status"] == "unknown"
    assert "MU_ASSET_PROBE" in result["why"]


def test_asset_probe_reads_env_and_checks_last_frame(tmp_path, monkeypatch):
    frame = make(tmp_path / "cyan.png")
    spec = cyan_spec(tmp_path / "spec.json")
    monkeypatch.setenv(asset_probe.ENV_ASSET_PROBE, str(spec))
    result = asset_probe.run(FakePilot(str(frame)))
    assert result["status"] == "pass"
    assert result["measures"]["colour_share"] == pytest.approx(1.0)


def test_asset_probe_captures_when_the_pilot_has_no_frame(tmp_path, monkeypatch):
    frame = make(tmp_path / "cyan.png")
    spec = cyan_spec(tmp_path / "spec.json")
    monkeypatch.setenv(asset_probe.ENV_ASSET_PROBE, str(spec))

    class LazyPilot(FakePilot):
        def __init__(self, png):
            super().__init__(None)
            self.frame = str(png)

        def look(self):
            self.looked = True
            self.last_png = self.frame

    pilot = LazyPilot(frame)
    result = asset_probe.run(pilot)
    assert pilot.looked is True
    assert result["status"] == "pass"


def test_asset_probe_unknown_for_missing_spec_file(tmp_path, monkeypatch):
    monkeypatch.setenv(asset_probe.ENV_ASSET_PROBE, str(tmp_path / "missing.json"))
    result = asset_probe.run(FakePilot())
    assert result["status"] == "unknown"
    assert "cannot read" in result["why"]


def test_asset_probe_run_frame_matches_check_frame(tmp_path):
    frame = make(tmp_path / "cyan.png")
    spec = {"region": [0.0, 0.0, 1.0, 1.0],
            "colour": {"hue": list(CYAN_HUE), "pass_at": 0.02}}
    assert asset_probe.run_frame(frame, spec) == assets.check_frame(frame, spec)
