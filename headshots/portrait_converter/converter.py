"""Core Portrait Converter Engine for Project Mu.

Normalizes incoming cutout headshots (e.g. from static/players/{id}.webp),
frames and centers the subject, applies optional headroom and chin padding to
match standard Madden portrait aspect ratios and dimensions (512x512, 256x256),
and encodes to DirectDraw Surface (DDS BC3/BC1) or raw RGBA texture bytes.
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Sequence
from PIL import Image

from headshots.portrait_converter.dds import encode_dds


def frame_headshot(
    image: Image.Image,
    width: int = 512,
    height: int = 512,
    crop_box: tuple[int, int, int, int] | None = None,
    headroom_ratio: float = 0.05,
    bottom_padding_ratio: float = 0.0,
    side_margin_ratio: float = 0.03,
) -> Image.Image:
    """Frame and center a cutout headshot on a transparent canvas.

    Normalizes subjects by detecting their alpha bounding box, scaling uniformly
    to fit within specified margins without aspect distortion, centering horizontally,
    and anchoring vertically with configured headroom.

    Args:
        image: Source PIL Image (RGB or RGBA).
        width: Destination canvas width in pixels.
        height: Destination canvas height in pixels.
        crop_box: Optional (left, top, right, bottom) pixel coordinates to crop first.
        headroom_ratio: Top headroom margin as fraction of height (default 0.05 = 5%).
        bottom_padding_ratio: Bottom chin/chest padding as fraction of height (default 0.0).
        side_margin_ratio: Minimum side margin as fraction of width (default 0.03 = 3%).

    Returns:
        New RGBA PIL Image of exactly (width, height) with transparent background.
    """
    if image.mode != "RGBA":
        image = image.convert("RGBA")

    if crop_box is not None:
        image = image.crop(crop_box)

    alpha = image.split()[-1]
    bbox = alpha.getbbox()

    if not bbox:
        # Fully transparent input
        return Image.new("RGBA", (width, height), (0, 0, 0, 0))

    bx0, by0, bx1, by1 = bbox
    subj_w = max(1, bx1 - bx0)
    subj_h = max(1, by1 - by0)

    # Compute maximum available dimensions within canvas constraints
    avail_w = max(1.0, width * (1.0 - 2.0 * side_margin_ratio))
    avail_h = max(1.0, height * (1.0 - headroom_ratio - bottom_padding_ratio))

    scale = min(avail_w / subj_w, avail_h / subj_h)
    new_w = max(1, int(round(subj_w * scale)))
    new_h = max(1, int(round(subj_h * scale)))

    cropped_subj = image.crop((bx0, by0, bx1, by1))
    resized_subj = cropped_subj.resize((new_w, new_h), Image.Resampling.LANCZOS)

    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    paste_x = (width - new_w) // 2

    # Player portraits anchor chest/shoulders to bottom margin
    paste_y = height - new_h - int(round(height * bottom_padding_ratio))
    top_min = int(round(height * headroom_ratio))
    paste_y = max(top_min, paste_y)

    canvas.paste(resized_subj, (paste_x, paste_y), resized_subj)
    return canvas


def to_portrait(
    webp_bytes: bytes,
    width: int = 512,
    height: int = 512,
    fmt: str = "DDS_BC3",
    crop_box: tuple[int, int, int, int] | None = None,
    headroom_ratio: float = 0.05,
    bottom_padding_ratio: float = 0.0,
) -> bytes:
    """Convert raw image bytes (e.g. WebP) into portrait texture bytes.

    Args:
        webp_bytes: Source image bytes (WebP, PNG, etc.).
        width: Destination texture width (default 512).
        height: Destination texture height (default 512).
        fmt: Output format:
            - "DDS_BC3" / "BC3": DirectDraw Surface with BC3/DXT5 compression (recommended).
            - "DDS_BC1" / "BC1": DirectDraw Surface with BC1/DXT1 1-bit alpha.
            - "DDS_RGBA": DirectDraw Surface with uncompressed 32-bit RGBA.
            - "RGBA": Raw uncompressed 32-bit RGBA pixel bytes (width * height * 4).
        crop_box: Optional (left, top, right, bottom) pixel coordinates.
        headroom_ratio: Headroom padding ratio (default 0.05).
        bottom_padding_ratio: Bottom padding ratio (default 0.0).

    Returns:
        DDS file bytes or raw RGBA bytes.
    """
    if not webp_bytes:
        raise ValueError("Input webp_bytes cannot be empty")

    src_image = Image.open(io.BytesIO(webp_bytes))
    framed_image = frame_headshot(
        src_image,
        width=width,
        height=height,
        crop_box=crop_box,
        headroom_ratio=headroom_ratio,
        bottom_padding_ratio=bottom_padding_ratio,
    )

    fmt_clean = fmt.strip().upper()

    if fmt_clean == "RGBA":
        return framed_image.tobytes("raw", "RGBA")

    # DDS formats
    return encode_dds(framed_image, fmt=fmt_clean)


def convert_headshot_file(
    input_path: str | Path,
    output_path: str | Path,
    width: int = 512,
    height: int = 512,
    fmt: str = "DDS_BC3",
    crop_box: tuple[int, int, int, int] | None = None,
    **kwargs,
) -> Path:
    """Read a headshot file, convert to portrait texture, and save to disk.

    Args:
        input_path: Path to source image file (WebP, PNG, etc.).
        output_path: Path to destination file (e.g. output.dds).
        width: Destination width (default 512).
        height: Destination height (default 512).
        fmt: Target format ("DDS_BC3", "DDS_BC1", "RGBA").
        crop_box: Optional crop bounding box.
        **kwargs: Additional framing options passed to to_portrait.

    Returns:
        Path object of written output file.
    """
    in_p = Path(input_path)
    out_p = Path(output_path)

    if not in_p.exists():
        raise FileNotFoundError(f"Source headshot file not found: {in_p}")

    webp_bytes = in_p.read_bytes()
    portrait_bytes = to_portrait(
        webp_bytes,
        width=width,
        height=height,
        fmt=fmt,
        crop_box=crop_box,
        **kwargs,
    )

    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_bytes(portrait_bytes)
    return out_p


def convert_headshots_batch(
    input_dir: str | Path,
    output_dir: str | Path,
    width: int = 512,
    height: int = 512,
    fmt: str = "DDS_BC3",
    limit: int | None = None,
    pattern: str = "*.webp",
) -> list[Path]:
    """Batch convert all matching headshots in an input directory.

    Args:
        input_dir: Directory containing source WebP files.
        output_dir: Directory to save converted textures.
        width: Output texture width.
        height: Output texture height.
        fmt: Output format (e.g. "DDS_BC3").
        limit: Optional maximum number of images to convert.
        pattern: Glob pattern to match files (default '*.webp').

    Returns:
        List of generated output Paths.
    """
    in_dir = Path(input_dir)
    out_dir = Path(output_dir)

    if not in_dir.exists():
        raise FileNotFoundError(f"Input directory does not exist: {in_dir}")

    out_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(
        in_dir.glob(pattern),
        key=lambda p: (int(p.stem) if p.stem.isdigit() else p.stem),
    )
    if limit is not None and limit > 0:
        files = files[:limit]

    ext = ".dds" if "DDS" in fmt.upper() or "BC" in fmt.upper() else ".rgba"
    output_paths = []

    for f in files:
        target_name = f.stem + ext
        target_path = out_dir / target_name
        convert_headshot_file(f, target_path, width=width, height=height, fmt=fmt)
        output_paths.append(target_path)

    return output_paths
