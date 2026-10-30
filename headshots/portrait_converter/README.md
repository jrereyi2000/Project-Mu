# Project Mu — Standalone Portrait Converter

Standalone utility that converts Project Mu player headshots (`static/players/{player_id}.webp`) into in-game player portrait textures for Madden 27 PC.

References:
- `docs/in_progress/PC_MIGRATION_PLAN.md` §9.4 (*Headshots as In-Game Portraits*)
- `docs/not_started/PRE_PC_WORK_2.md` Item A5 (*Portrait Converter*)

---

## Overview

Project Mu generates broadcast-grade media-day player headshots as transparent WebP cutouts (typically 700×508 pixels). In Madden 27, player cards, depth charts, menus, and broadcast presentation cards display 2D portraits referenced by `Player.PLYR_PORTRAIT`.

This package converts those WebP cutouts into standard DirectDraw Surface (`.dds`) textures formatted for injection into Madden's `library_icons_brt` asset bundle via community modding tools (MMC Modding Tools or FMT).

### Key Features
1. **Pure Python & NumPy DDS Compression:**
   - Implements native block compression for **BC3 / DXT5** (interpolated 8-level alpha) and **BC1 / DXT1** (1-bit alpha punchthrough).
   - Requires zero external native C DLLs or compilers; runs cross-platform on macOS, Linux, and Windows.
   - Vectorized with NumPy for high throughput (encodes a 512×512 texture in ~60ms).
2. **Binary Direct3D DDS Header:**
   - Adheres to standard 128-byte Direct3D binary layout (`b"DDS "` magic + 124-byte `DDS_HEADER` struct).
   - Generates exact FourCC codes (`b"DXT5"`, `b"DXT1"`) and D3D pixel format flags.
3. **Intelligent Auto-Framing & Padding:**
   - Analyzes alpha channel bounding boxes of incoming cutouts.
   - Scales the player bust uniformly without aspect distortion.
   - Centers the subject horizontally and anchors the chest/shoulders to the bottom while maintaining a safe headroom margin (default 5%) so helmets and hair are not clipped.
4. **Flexible Output Formats:**
   - `DDS_BC3` (or `BC3`): High-fidelity smooth alpha transparency (recommended for Madden).
   - `DDS_BC1` (or `BC1`): Compact 1-bit alpha transparency.
   - `DDS_RGBA`: Uncompressed 32-bit BGRA DirectDraw Surface.
   - `RGBA`: Raw 32-bit RGBA texture byte array (`width * height * 4`).

---

## Python API

### Core Function: `to_portrait`
```python
from headshots.portrait_converter import to_portrait

# Read source WebP bytes
with open("static/players/1.webp", "rb") as f:
    webp_data = f.read()

# Convert to 512x512 BC3 DDS bytes
dds_bytes = to_portrait(
    webp_bytes=webp_data,
    width=512,
    height=512,
    fmt="DDS_BC3",  # "DDS_BC3", "DDS_BC1", "RGBA", or "DDS_RGBA"
    crop_box=None,   # Optional (xmin, ymin, xmax, ymax)
    headroom_ratio=0.05,
)

with open("portraits/1.dds", "wb") as f:
    f.write(dds_bytes)
```

### File Conversion: `convert_headshot_file`
```python
from headshots.portrait_converter import convert_headshot_file

out_path = convert_headshot_file(
    input_path="static/players/1.webp",
    output_path="portraits/1.dds",
    width=512,
    height=512,
    fmt="DDS_BC3",
)
print(f"Generated: {out_path}")
```

### Batch Conversion: `convert_headshots_batch`
```python
from headshots.portrait_converter import convert_headshots_batch

paths = convert_headshots_batch(
    input_dir="static/players",
    output_dir="portraits",
    width=512,
    height=512,
    fmt="DDS_BC3",
    limit=10,
)
print(f"Converted {len(paths)} portraits.")
```

---

## Command Line Interface (CLI)

The package provides a standalone executable module:

```bash
# Single file conversion
python -m headshots.portrait_converter.cli static/players/1.webp --out portraits/1.dds

# Custom dimensions and format
python -m headshots.portrait_converter.cli static/players/1.webp --out portraits/1.dds --width 256 --height 256 --fmt BC1

# Batch directory conversion with limit
python -m headshots.portrait_converter.cli --batch-dir static/players/ --out-dir portraits/ --limit 50 --width 512 --height 512

# Manual crop and custom headroom
python -m headshots.portrait_converter.cli static/players/1.webp --out 1_cropped.dds --crop 50 10 650 500 --headroom 0.08
```

### CLI Arguments
- `input`: Path to source WebP/image file (single-file mode).
- `--out`, `-o`: Path to output file (single-file mode).
- `--batch-dir`: Input folder for batch conversion.
- `--out-dir`: Destination folder for batch conversion (default: `portraits/`).
- `--limit`: Maximum number of files to process in batch mode.
- `--width`: Texture width in pixels (default: `512`).
- `--height`: Texture height in pixels (default: `512`).
- `--fmt`: Format: `DDS_BC3` (default), `DDS_BC1`, or `RGBA`.
- `--crop`: Optional bounding box coordinates `XMIN YMIN XMAX YMAX`.
- `--headroom`: Top margin fraction (default: `0.05`).
- `--bottom-padding`: Bottom padding fraction (default: `0.0`).
- `--quiet`, `-q`: Suppress progress output.

---

## Technical Specifications

### Binary DDS Header Layout (128 bytes)
| Offset | Field | Type | Description |
| :--- | :--- | :--- | :--- |
| `0x00` | `dwMagic` | `char[4]` | `b"DDS "` (0x20534444) |
| `0x04` | `dwSize` | `uint32` | Header size (124) |
| `0x08` | `dwFlags` | `uint32` | `DDSD_CAPS \| DDSD_HEIGHT \| DDSD_WIDTH \| DDSD_PIXELFORMAT \| DDSD_LINEARSIZE` (`0x81007`) |
| `0x0C` | `dwHeight` | `uint32` | Texture height in pixels (e.g. 512) |
| `0x10` | `dwWidth` | `uint32` | Texture width in pixels (e.g. 512) |
| `0x14` | `dwPitchOrLinearSize` | `uint32` | Total top-level byte size (`width/4 * height/4 * block_size`) |
| `0x18` | `dwDepth` | `uint32` | 0 |
| `0x1C` | `dwMipMapCount` | `uint32` | 0 (1 top-level mip) |
| `0x20` | `dwReserved1[11]`| `uint32[11]` | 0 |
| `0x4C` | `ddspf.dwSize` | `uint32` | 32 |
| `0x50` | `ddspf.dwFlags`| `uint32` | `DDPF_FOURCC` (`0x04`) or `DDPF_RGB \| DDPF_ALPHAPIXELS` (`0x41`) |
| `0x54` | `ddspf.dwFourCC`| `char[4]` | `b"DXT5"` or `b"DXT1"` |
| `0x58` | `ddspf.dwRGBBitCount` | `uint32` | 0 for compressed, 32 for uncompressed |
| `0x5C` | `ddspf.dwR/G/B/ABitMask` | `uint32[4]` | Channel masks |
| `0x6C` | `dwCaps` | `uint32` | `DDSCAPS_TEXTURE` (`0x1000`) |
| `0x70` | `dwCaps2..4` | `uint32[3]` | 0 |
| `0x7C` | `dwReserved2` | `uint32` | 0 |

### Madden Modding Integration
1. Real players in Madden occupy portrait slots in `library_icons_brt` (keyed by `Player.PLYR_PORTRAIT`).
2. Converted 512×512 BC3 `.dds` textures replace the retired player portrait assets.
3. Assets are loaded into Madden 27 via the Frosty / MMC Modding Tools mod framework.
