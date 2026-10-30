"""Texture recolouring on RGBA PNGs (MOD_BUILDER_PLAN §2.1) and RGBA8 DDS output for the harness's import.

Modes (alpha is kept unless keep_alpha is false, which makes the texture opaque):
* tint      {"color": "#FFD400"}: every pixel takes the colour's hue and keeps its own saturation and value,
            so shading survives. This is what the spike did to the first-down swatch (hue 50°, S and V kept).
* hue-shift {"degrees": 30}: rotate every pixel's hue.
* map       {"map": [["#FC6A29", "#FFD400", 40], ...]}: pixels within `tolerance` (largest channel
            difference) of `from` move by (to - from), so anti-aliased edges keep their variation.
"""
import numpy as np
from PIL import Image

from headshots.portrait_converter.dds import build_dds_header


def hex_rgb(color):
    c = color.lstrip("#")
    return np.array([int(c[i:i + 2], 16) for i in (0, 2, 4)], dtype=np.float64)


def rgb_to_hsv(rgb):
    """rgb floats 0–1, shape (..., 3) -> h (degrees 0–360), s, v."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    v = rgb.max(axis=-1)
    c = v - rgb.min(axis=-1)
    s = np.where(v > 0, c / np.where(v > 0, v, 1), 0.0)
    safe = np.where(c > 0, c, 1)
    h = np.where(v == r, ((g - b) / safe) % 6,
                 np.where(v == g, (b - r) / safe + 2, (r - g) / safe + 4))
    h = np.where(c > 0, h * 60.0, 0.0)
    return h, s, v


def hsv_to_rgb(h, s, v):
    h = (h % 360.0) / 60.0
    c = v * s
    x = c * (1 - np.abs(h % 2 - 1))
    m = v - c
    z = np.zeros_like(h)
    sector = np.floor(h).astype(int) % 6
    choices = [np.stack(t, axis=-1) for t in ((c, x, z), (x, c, z), (z, c, x), (z, x, c), (x, z, c), (c, z, x))]
    out = np.zeros(h.shape + (3,))
    for i, ch in enumerate(choices):
        out = np.where((sector == i)[..., None], ch, out)
    return out + m[..., None]


def recolor_array(rgba, spec):
    """uint8 RGBA array (H, W, 4) -> recoloured uint8 RGBA array."""
    rgba = np.asarray(rgba, dtype=np.uint8)
    rgb = rgba[..., :3].astype(np.float64)
    mode = spec["mode"]
    if mode in ("tint", "hue-shift"):
        h, s, v = rgb_to_hsv(rgb / 255.0)
        if mode == "tint":
            th, _, _ = rgb_to_hsv(hex_rgb(spec["color"]) / 255.0)
            h = np.full_like(h, float(th))
        else:
            h = h + float(spec["degrees"])
        new = hsv_to_rgb(h, s, v) * 255.0
    elif mode == "map":
        new = rgb.copy()
        for src, dst, tol in spec["map"]:
            a, b = hex_rgb(src), hex_rgb(dst)
            hit = np.abs(rgb - a).max(axis=-1) <= tol
            new[hit] = rgb[hit] + (b - a)
    else:
        raise ValueError(f"unknown recolor mode {mode!r}")
    out = rgba.copy()
    out[..., :3] = np.clip(np.rint(new), 0, 255).astype(np.uint8)
    if not spec.get("keep_alpha", True):
        out[..., 3] = 255
    return out


def recolor_png(src, dst, spec):
    """Recolour an image file (any mode Pillow reads; written as RGBA PNG)."""
    img = Image.open(src).convert("RGBA")
    Image.fromarray(recolor_array(np.array(img), spec), "RGBA").save(dst, "PNG")
    return dst


def mip_chain(rgba):
    """Box-filtered mips from (H, W, 4) down to 1x1, the top level first."""
    level = np.asarray(rgba, dtype=np.float64)
    out = [np.asarray(rgba, dtype=np.uint8)]
    while level.shape[0] > 1 or level.shape[1] > 1:
        if level.shape[0] > 1:
            n = level.shape[0] // 2 * 2
            level = (level[0:n:2] + level[1:n:2]) / 2
        if level.shape[1] > 1:
            n = level.shape[1] // 2 * 2
            level = (level[:, 0:n:2] + level[:, 1:n:2]) / 2
        out.append(np.clip(np.rint(level), 0, 255).astype(np.uint8))
    return out


def png_to_dds(png, dds_path):
    """Write an uncompressed RGBA8 DDS (BGRA in memory, the header's masks) with a full mip chain."""
    rgba = np.array(Image.open(png).convert("RGBA"))
    h, w = rgba.shape[:2]
    mips = mip_chain(rgba)
    data = bytearray(build_dds_header(w, h, fmt="RGBA", mip_count=len(mips)))
    for m in mips:
        data += m[..., [2, 1, 0, 3]].tobytes()
    with open(dds_path, "wb") as f:
        f.write(data)
    return len(mips)
