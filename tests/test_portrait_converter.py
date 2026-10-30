"""Unit tests for Project Mu Standalone Portrait Converter.

Validates:
  - Binary DDS header generation and field specifications (DXT5, DXT1, RGBA).
  - Pure Python / NumPy block compression (BC3/DXT5 and BC1/DXT1).
  - Alpha transparency preservation and Pillow round-trip decoding.
  - Headshot auto-framing, subject normalization, and headroom padding.
  - Single-file and batch CLI conversion workflows.
"""

from __future__ import annotations

import io
from pathlib import Path
import numpy as np
import pytest
from PIL import Image

from headshots.portrait_converter import (
    build_dds_header,
    convert_headshot_file,
    convert_headshots_batch,
    decode_dds_header,
    encode_bc1_blocks,
    encode_bc3_blocks,
    encode_dds,
    frame_headshot,
    to_portrait,
)
from headshots.portrait_converter import cli


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_webp_bytes() -> bytes:
    """Provide real player WebP bytes from static/players/ if available, or generate synthetic."""
    sample_path = Path("static/players/1.webp")
    if sample_path.exists():
        return sample_path.read_bytes()

    # Generate synthetic 700x508 RGBA WebP with transparent background and subject
    im = Image.new("RGBA", (700, 508), (0, 0, 0, 0))
    # Draw simple subject in middle
    for y in range(100, 508):
        for x in range(200, 500):
            im.putpixel((x, y), (180, 100, 50, 255))
    bio = io.BytesIO()
    im.save(bio, format="WEBP")
    return bio.getvalue()


@pytest.fixture
def synthetic_rgba_image() -> Image.Image:
    """Generate a 64x64 synthetic RGBA image with diverse alpha patterns."""
    im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    for y in range(64):
        for x in range(64):
            # Alpha gradient with colored shapes
            alpha = int((x / 64.0) * 255)
            r = (x * 4) % 256
            g = (y * 4) % 256
            b = ((x + y) * 2) % 256
            im.putpixel((x, y), (r, g, b, alpha))
    return im


# ---------------------------------------------------------------------------
# DDS Header Tests
# ---------------------------------------------------------------------------

def test_dds_header_bc3_dxt5():
    """Verify standard 128-byte DDS header structure for BC3/DXT5."""
    hdr = build_dds_header(512, 512, fmt="BC3")
    assert len(hdr) == 128
    assert hdr[:4] == b"DDS "

    parsed = decode_dds_header(hdr)
    assert parsed["magic"] == b"DDS "
    assert parsed["header_size"] == 124
    assert parsed["width"] == 512
    assert parsed["height"] == 512
    assert parsed["fourcc"] == "DXT5"
    assert parsed["format"] == "BC3"
    assert parsed["pitch_or_linear"] == (512 // 4) * (512 // 4) * 16  # 262144
    assert parsed["caps"] == 0x1000


def test_dds_header_bc1_dxt1():
    """Verify standard 128-byte DDS header structure for BC1/DXT1."""
    hdr = build_dds_header(256, 256, fmt="BC1")
    assert len(hdr) == 128

    parsed = decode_dds_header(hdr)
    assert parsed["width"] == 256
    assert parsed["height"] == 256
    assert parsed["fourcc"] == "DXT1"
    assert parsed["format"] == "BC1"
    assert parsed["pitch_or_linear"] == (256 // 4) * (256 // 4) * 8  # 32768


def test_dds_header_rgba():
    """Verify DDS header for uncompressed 32-bit RGBA texture."""
    hdr = build_dds_header(128, 128, fmt="RGBA")
    assert len(hdr) == 128

    parsed = decode_dds_header(hdr)
    assert parsed["width"] == 128
    assert parsed["height"] == 128
    assert parsed["fourcc"] == ""
    assert parsed["format"] == "RGBA"
    assert parsed["rgb_bit_count"] == 32
    assert parsed["pitch_or_linear"] == 128 * 4


def test_decode_dds_header_validation():
    """Verify header validator rejects truncated data and invalid magic."""
    with pytest.raises(ValueError, match="too short"):
        decode_dds_header(b"DDS " + bytes(50))

    with pytest.raises(ValueError, match="Invalid DDS magic"):
        decode_dds_header(b"FAIL" + bytes(124))


# ---------------------------------------------------------------------------
# Pure Python Block Compression Tests
# ---------------------------------------------------------------------------

def test_encode_bc3_synthetic_blocks():
    """Verify BC3 block compressor preserves alpha and colors across edge cases."""
    # 4x4 block with distinct colors and full alpha range (0, 64, 128, 255)
    block_arr = np.zeros((4, 4, 4), dtype=np.uint8)
    block_arr[0, :] = [255, 0, 0, 0]      # Transparent red
    block_arr[1, :] = [0, 255, 0, 85]     # Semi-transparent green
    block_arr[2, :] = [0, 0, 255, 170]    # Semi-transparent blue
    block_arr[3, :] = [255, 255, 255, 255]# Opaque white

    compressed_bytes = encode_bc3_blocks(block_arr)
    assert len(compressed_bytes) == 16  # 1 block = 16 bytes

    # Assemble into full DDS and decode with Pillow
    hdr = build_dds_header(4, 4, fmt="BC3")
    dds_full = hdr + compressed_bytes
    decoded = Image.open(io.BytesIO(dds_full))

    assert decoded.size == (4, 4)
    assert decoded.mode == "RGBA"

    dec_arr = np.array(decoded)
    # Check row 0 has alpha close to 0
    assert np.all(dec_arr[0, :, 3] <= 10)
    # Check row 3 has alpha close to 255
    assert np.all(dec_arr[3, :, 3] >= 245)


def test_encode_bc1_punchthrough_alpha():
    """Verify BC1 1-bit alpha punchthrough logic."""
    block_arr = np.full((4, 4, 4), 200, dtype=np.uint8)
    # Set top-left pixel to transparent
    block_arr[0, 0, 3] = 0

    compressed_bytes = encode_bc1_blocks(block_arr)
    assert len(compressed_bytes) == 8  # 1 block = 8 bytes

    hdr = build_dds_header(4, 4, fmt="BC1")
    decoded = Image.open(io.BytesIO(hdr + compressed_bytes))
    dec_arr = np.array(decoded)

    # Pixel (0, 0) must decode as fully transparent (alpha=0)
    assert dec_arr[0, 0, 3] == 0
    # Other pixels must be opaque (alpha=255)
    assert dec_arr[0, 1, 3] == 255
    assert dec_arr[3, 3, 3] == 255


def test_encode_dds_synthetic_roundtrip(synthetic_rgba_image):
    """Verify full encode_dds round-trip decode with Pillow on 64x64 texture."""
    dds_bc3 = encode_dds(synthetic_rgba_image, fmt="DDS_BC3")
    assert len(dds_bc3) == 128 + (64 // 4) * (64 // 4) * 16

    decoded = Image.open(io.BytesIO(dds_bc3))
    assert decoded.size == (64, 64)
    assert decoded.mode == "RGBA"

    orig_arr = np.array(synthetic_rgba_image)
    dec_arr = np.array(decoded)

    # Validate high correlation / low MSE
    mse = np.mean((orig_arr.astype(float) - dec_arr.astype(float)) ** 2)
    assert mse < 100.0  # High compression fidelity


# ---------------------------------------------------------------------------
# Framing & Subject Normalization Tests
# ---------------------------------------------------------------------------

def test_frame_headshot_centering_and_headroom():
    """Verify frame_headshot centers subject horizontally and preserves headroom."""
    # Create 300x200 canvas with a 100x100 subject positioned off-center
    src = Image.new("RGBA", (300, 200), (0, 0, 0, 0))
    for y in range(50, 150):
        for x in range(20, 120):
            src.putpixel((x, y), (255, 128, 0, 255))

    framed = frame_headshot(src, width=512, height=512, headroom_ratio=0.05)
    assert framed.size == (512, 512)
    assert framed.mode == "RGBA"

    bbox = framed.getbbox()
    assert bbox is not None
    bx0, by0, bx1, by1 = bbox

    # Horizontal center should be near 256
    center_x = (bx0 + bx1) / 2.0
    assert abs(center_x - 256) <= 2

    # Top headroom should be at or below 5% (512 * 0.05 = ~25px)
    assert by0 >= int(512 * 0.05)


def test_frame_headshot_crop_box():
    """Verify manual crop_box is applied before canvas placement."""
    src = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
    # Two distinct boxes
    for y in range(50, 150):
        for x in range(50, 150):
            src.putpixel((x, y), (255, 0, 0, 255))
    for y in range(250, 350):
        for x in range(250, 350):
            src.putpixel((x, y), (0, 255, 0, 255))

    # Crop only the red box
    framed = frame_headshot(src, width=256, height=256, crop_box=(40, 40, 160, 160))
    assert framed.size == (256, 256)
    arr = np.array(framed)
    # Green channel should be 0 since the green box was excluded
    assert np.max(arr[:, :, 1]) == 0


def test_frame_headshot_empty():
    """Verify fully transparent input gracefully produces transparent output canvas."""
    empty = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    framed = frame_headshot(empty, width=512, height=512)
    assert framed.size == (512, 512)
    assert framed.getbbox() is None


# ---------------------------------------------------------------------------
# High-Level to_portrait Interface Tests
# ---------------------------------------------------------------------------

def test_to_portrait_bc3(sample_webp_bytes):
    """Test to_portrait with default DDS_BC3 format."""
    dds_bytes = to_portrait(sample_webp_bytes, width=512, height=512, fmt="DDS_BC3")
    assert dds_bytes.startswith(b"DDS ")
    assert len(dds_bytes) == 128 + 512 * 512  # Header + BC3 data

    # Decode with Pillow
    im = Image.open(io.BytesIO(dds_bytes))
    assert im.size == (512, 512)
    assert im.mode == "RGBA"

    # Verify alpha channel was preserved
    arr = np.array(im)
    assert np.any(arr[:, :, 3] > 0)
    assert np.any(arr[:, :, 3] == 0)


def test_to_portrait_bc1(sample_webp_bytes):
    """Test to_portrait with DDS_BC1 format."""
    dds_bytes = to_portrait(sample_webp_bytes, width=256, height=256, fmt="DDS_BC1")
    assert dds_bytes.startswith(b"DDS ")
    assert len(dds_bytes) == 128 + (256 * 256 // 2)

    im = Image.open(io.BytesIO(dds_bytes))
    assert im.size == (256, 256)
    assert im.mode == "RGBA"


def test_to_portrait_rgba_raw(sample_webp_bytes):
    """Test to_portrait with raw RGBA bytes format."""
    rgba_bytes = to_portrait(sample_webp_bytes, width=128, height=128, fmt="RGBA")
    assert not rgba_bytes.startswith(b"DDS ")
    assert len(rgba_bytes) == 128 * 128 * 4


def test_to_portrait_invalid_format(sample_webp_bytes):
    """Test to_portrait raises ValueError on unknown format."""
    with pytest.raises(ValueError, match="Unsupported format"):
        to_portrait(sample_webp_bytes, fmt="INVALID_FORMAT")


def test_to_portrait_empty_input():
    """Test to_portrait raises ValueError on empty input."""
    with pytest.raises(ValueError, match="cannot be empty"):
        to_portrait(b"")


# ---------------------------------------------------------------------------
# File and Batch Conversion Helper Tests
# ---------------------------------------------------------------------------

def test_convert_headshot_file(tmp_path, sample_webp_bytes):
    """Verify convert_headshot_file writes valid DDS file to disk."""
    in_file = tmp_path / "player_1.webp"
    in_file.write_bytes(sample_webp_bytes)

    out_file = tmp_path / "portraits" / "player_1.dds"
    res_path = convert_headshot_file(
        input_path=in_file,
        output_path=out_file,
        width=512,
        height=512,
        fmt="DDS_BC3",
    )

    assert res_path == out_file
    assert out_file.exists()
    assert out_file.stat().st_size == 128 + 512 * 512

    # Verify file can be read back as DDS
    parsed = decode_dds_header(out_file.read_bytes()[:128])
    assert parsed["format"] == "BC3"
    assert parsed["width"] == 512


def test_convert_headshot_file_missing_input(tmp_path):
    """Verify convert_headshot_file raises FileNotFoundError on missing file."""
    with pytest.raises(FileNotFoundError):
        convert_headshot_file(tmp_path / "missing.webp", tmp_path / "out.dds")


def test_convert_headshots_batch_mixed_names(tmp_path, sample_webp_bytes):
    """Numeric and non-numeric filenames in one folder sort without TypeError."""
    in_dir = tmp_path / "mixed_in"
    in_dir.mkdir()
    for name in ["10", "abc", "2", "Zed"]:
        (in_dir / f"{name}.webp").write_bytes(sample_webp_bytes)

    outputs = convert_headshots_batch(in_dir, tmp_path / "mixed_out", width=64, height=64)

    assert [p.stem for p in outputs] == ["2", "10", "Zed", "abc"]


def test_convert_headshots_batch(tmp_path, sample_webp_bytes):
    """Verify convert_headshots_batch processes multiple files with limits."""
    in_dir = tmp_path / "batch_in"
    in_dir.mkdir()
    out_dir = tmp_path / "batch_out"

    # Create 3 dummy WebPs
    for i in range(1, 4):
        (in_dir / f"{i}.webp").write_bytes(sample_webp_bytes)

    # Convert with limit=2
    outputs = convert_headshots_batch(
        input_dir=in_dir,
        output_dir=out_dir,
        width=256,
        height=256,
        fmt="DDS_BC3",
        limit=2,
    )

    assert len(outputs) == 2
    for p in outputs:
        assert p.exists()
        assert p.suffix == ".dds"
        assert p.stat().st_size == 128 + 256 * 256


# ---------------------------------------------------------------------------
# CLI Tests
# ---------------------------------------------------------------------------

def test_cli_single_file_conversion(tmp_path, sample_webp_bytes):
    """Test CLI single file conversion execution."""
    in_file = tmp_path / "input.webp"
    in_file.write_bytes(sample_webp_bytes)
    out_file = tmp_path / "output.dds"

    exit_code = cli.main([
        str(in_file),
        "--out", str(out_file),
        "--width", "256",
        "--height", "256",
        "--fmt", "BC3",
        "--quiet",
    ])

    assert exit_code == 0
    assert out_file.exists()
    assert out_file.stat().st_size == 128 + 256 * 256


def test_cli_batch_directory_conversion(tmp_path, sample_webp_bytes):
    """Test CLI batch directory conversion."""
    in_dir = tmp_path / "players"
    in_dir.mkdir()
    for i in range(1, 4):
        (in_dir / f"{i}.webp").write_bytes(sample_webp_bytes)

    out_dir = tmp_path / "output_portraits"

    exit_code = cli.main([
        "--batch-dir", str(in_dir),
        "--out-dir", str(out_dir),
        "--limit", "2",
        "--width", "128",
        "--height", "128",
        "--fmt", "DDS_BC1",
        "--quiet",
    ])

    assert exit_code == 0
    generated = list(out_dir.glob("*.dds"))
    assert len(generated) == 2


def test_cli_empty_args_shows_help():
    """Test CLI without arguments returns error code 1."""
    assert cli.main([]) == 1


def test_cli_nonexistent_input():
    """Test CLI with invalid input path returns error code 1."""
    assert cli.main(["nonexistent_file.webp", "--out", "out.dds", "--quiet"]) == 1
