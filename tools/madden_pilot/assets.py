"""Fixed asset checks for the Madden pilot (MADDEN_SPIKE_AGENT_PLAN section 2.8).

These are pure pixel measurements, never model judgements:

* ``colour_share`` - the share of pixels in a named region inside a hue band, with a minimum
  saturation and value.
* ``crop_similarity`` - SSIM on grey after resize, 0..1, between a frame region and a
  reference crop.
* ``check_frame`` - applies one check spec and returns pass / fail / unknown.

The both-ways rule keeps a check from guessing: it passes only when every given measure
passes, fails only when every measure points the other way, and is unknown otherwise. A
colour measure passes at or above ``pass_at``; a reference measure passes when the similarity
to ``pass`` beats the similarity to ``fail`` by ``margin``, and points the other way when the
similarity to ``fail`` beats ``pass`` by the same margin.

Pillow is required. numpy is used when present (it is in the project ``.venv``); without it
``colour_share`` loops over the Pillow HSV pixels and ``crop_similarity`` falls back to a mean
absolute-difference similarity.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image

try:                                        # .venv has numpy; the fallback keeps the checks usable
    import numpy as np
except ImportError:                         # pragma: no cover - exercised only on a numpy-less host
    np = None

Region = tuple[float, float, float, float]  # x, y, w, h as fractions of the frame

ROOT = Path(__file__).resolve().parents[2]


def _resample() -> Any:
    return getattr(getattr(Image, "Resampling", Image), "LANCZOS")


def _crop_box(img: Image.Image, region: Region | None) -> tuple[int, int, int, int] | None:
    """Pixel box for a fractional region, clamped to the image; None for an empty box."""
    width, height = img.size
    if region is None:
        return (0, 0, width, height)
    x, y, w, h = (float(value) for value in region)
    left = max(0, min(width, round(x * width)))
    top = max(0, min(height, round(y * height)))
    right = max(left, min(width, round((x + w) * width)))
    bottom = max(top, min(height, round((y + h) * height)))
    if right <= left or bottom <= top:
        return None
    return (left, top, right, bottom)


def colour_share(png: str | Path, region: Region, hue: tuple[float, float],
                 min_sat: float = 0.5, min_val: float = 0.4) -> float:
    """Share (0..1) of pixels in ``region`` whose hue is inside ``hue`` degrees and bright enough.

    A hue range may wrap (for example ``(350, 10)``). Saturation and value are compared on the
    0..1 scale. An empty region returns 0.0.
    """
    with Image.open(png) as opened:
        hsv = opened.convert("RGB").convert("HSV")
        box = _crop_box(hsv, region)
        if box is None:
            return 0.0
        crop = hsv.crop(box)
        lo, hi = float(hue[0]), float(hue[1])
        if np is not None:
            arr = np.asarray(crop, dtype=np.uint8)
            h = arr[..., 0].astype(np.float32) * (360.0 / 255.0)
            s = arr[..., 1].astype(np.float32) / 255.0
            v = arr[..., 2].astype(np.float32) / 255.0
            mask = (s >= min_sat) & (v >= min_val)
            if lo <= hi:
                mask &= (h >= lo) & (h <= hi)
            else:
                mask &= (h >= lo) | (h <= hi)
            return float(mask.mean()) if mask.size else 0.0
        hits = total = 0
        for h8, s8, v8 in crop.getdata():
            total += 1
            degree = h8 * 360.0 / 255.0
            in_hue = (lo <= degree <= hi) if lo <= hi else (degree >= lo or degree <= hi)
            if in_hue and s8 / 255.0 >= min_sat and v8 / 255.0 >= min_val:
                hits += 1
        return hits / total if total else 0.0


def _grey_crops(png: str | Path, region: Region | None,
                reference_png: str | Path, ref_region: Region | None,
                size: int = 128) -> tuple[Image.Image, Image.Image]:
    with Image.open(png) as opened:
        img = opened.convert("RGB")
        box = _crop_box(img, region)
        if box is None:
            raise ValueError("frame region is empty")
        first = img.crop(box).convert("L")
    with Image.open(reference_png) as opened:
        ref = opened.convert("RGB")
        ref_box = _crop_box(ref, ref_region)
        if ref_box is None:
            raise ValueError("reference region is empty")
        second = ref.crop(ref_box).convert("L")
    resample = _resample()
    return first.resize((size, size), resample), second.resize((size, size), resample)


def _ssim_numpy(first: Image.Image, second: Image.Image) -> float:
    a = np.asarray(first, dtype=np.float64)
    b = np.asarray(second, dtype=np.float64)
    c1 = (0.01 * 255.0) ** 2
    c2 = (0.03 * 255.0) ** 2
    win = min(7, a.shape[0], a.shape[1])
    if win < 2:
        mu_a, mu_b = a.mean(), b.mean()
        var_a, var_b = a.var(), b.var()
        cov = ((a - mu_a) * (b - mu_b)).mean()
        value = ((2 * mu_a * mu_b + c1) * (2 * cov + c2)) / \
            ((mu_a ** 2 + mu_b ** 2 + c1) * (var_a + var_b + c2))
        return float(min(1.0, max(0.0, value)))
    from numpy.lib.stride_tricks import sliding_window_view

    wa = sliding_window_view(a, (win, win))
    wb = sliding_window_view(b, (win, win))
    mu_a = wa.mean(axis=(-2, -1))
    mu_b = wb.mean(axis=(-2, -1))
    var_a = (wa * wa).mean(axis=(-2, -1)) - mu_a * mu_a
    var_b = (wb * wb).mean(axis=(-2, -1)) - mu_b * mu_b
    cov = (wa * wb).mean(axis=(-2, -1)) - mu_a * mu_b
    ssim_map = ((2 * mu_a * mu_b + c1) * (2 * cov + c2)) / \
        ((mu_a ** 2 + mu_b ** 2 + c1) * (var_a + var_b + c2))
    return float(min(1.0, max(0.0, ssim_map.mean())))


def _mse_similarity(first: Image.Image, second: Image.Image) -> float:
    a = list(first.getdata())
    b = list(second.getdata())
    if not a or len(a) != len(b):
        return 0.0
    mad = sum(abs(x - y) for x, y in zip(a, b)) / len(a)
    return max(0.0, 1.0 - mad / 255.0)


def crop_similarity(png: str | Path, region: Region, reference_png: str | Path,
                    ref_region: Region | None = None) -> float:
    """SSIM on grey after resize between a frame region and a reference crop, 0..1.

    ``ref_region`` defaults to the whole reference image. Identical crops score 1.0.
    """
    first, second = _grey_crops(png, region, reference_png, ref_region)
    if np is not None:
        return _ssim_numpy(first, second)
    return _mse_similarity(first, second)     # pragma: no cover - numpy is in .venv


def _path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _unknown(why: str, measures: dict | None = None) -> dict:
    return {"status": "unknown", "measures": measures or {}, "why": why}


def check_frame(png: str | Path, spec: dict) -> dict:
    """Run one check spec (section 2.8) and return ``{"status", "measures", "why"}``.

    ``spec`` holds ``region`` and either ``colour`` (``{"hue": [lo, hi], "pass_at": x,
    "min_sat": s, "min_val": v}``) or ``reference`` (``{"pass": path, "fail": path,
    "margin": x, "region": [...]}``), or both. Missing files, an empty region, a colour check
    without ``pass_at``, or no measures at all return unknown with a reason.
    """
    if not isinstance(spec, dict):
        return _unknown("check spec is not an object")
    region = tuple(spec.get("region") or (0.0, 0.0, 1.0, 1.0))
    measures: dict[str, Any] = {}
    votes: list[bool | None] = []
    reasons: list[str] = []
    try:
        colour = spec.get("colour")
        if isinstance(colour, dict) and colour.get("hue"):
            pass_at = colour.get("pass_at")
            if pass_at is None:
                return _unknown("colour check has no pass_at")
            share = colour_share(png, region, tuple(colour["hue"]),
                                 min_sat=float(colour.get("min_sat", 0.5)),
                                 min_val=float(colour.get("min_val", 0.4)))
            measures["colour_share"] = share
            measures["colour_pass_at"] = float(pass_at)
            hit = share >= float(pass_at)
            votes.append(hit)
            reasons.append(f"colour share {share:.3f} {'>=' if hit else '<'} {float(pass_at):g} "
                           f"(hue {colour['hue']})")

        reference = spec.get("reference")
        if isinstance(reference, dict) and reference.get("pass") and reference.get("fail"):
            margin = float(reference.get("margin", 0.05))
            ref_region = tuple(reference["region"]) if reference.get("region") else None
            sim_pass = crop_similarity(png, region, _path(reference["pass"]), ref_region)
            sim_fail = crop_similarity(png, region, _path(reference["fail"]), ref_region)
            measures.update(similarity_pass=sim_pass, similarity_fail=sim_fail,
                            reference_margin=margin)
            if sim_pass >= sim_fail + margin:
                votes.append(True)
                reasons.append(f"similarity pass {sim_pass:.3f} beats fail {sim_fail:.3f} "
                               f"by {margin:g}")
            elif sim_fail >= sim_pass + margin:
                votes.append(False)
                reasons.append(f"similarity fail {sim_fail:.3f} beats pass {sim_pass:.3f} "
                               f"by {margin:g}")
            else:
                votes.append(None)
                reasons.append(f"reference inconclusive (pass {sim_pass:.3f}, "
                               f"fail {sim_fail:.3f}, margin {margin:g})")
    except (OSError, ValueError) as exc:
        return _unknown(f"could not read a frame or reference: {exc}", measures)

    if not votes:
        return _unknown("the check has no measures (needs colour or reference)", measures)
    if all(vote is True for vote in votes):
        status = "pass"
    elif all(vote is False for vote in votes):
        status = "fail"
    else:
        status = "unknown"
    return {"status": status, "measures": measures, "why": "; ".join(reasons)}


def load_spec(path: str | Path) -> dict | list:
    """Read a check spec JSON file (one spec, or a list of specs)."""
    return json.loads(Path(path).read_text(encoding="utf-8"))
