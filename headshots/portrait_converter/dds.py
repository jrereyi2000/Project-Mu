"""Pure Python / NumPy DDS Encoder for Project Mu.

Encodes RGBA textures into DirectDraw Surface (DDS) container format supporting:
  - BC3 / DXT5 (interpolated alpha, high-quality cutout portraits)
  - BC1 / DXT1 (1-bit alpha or opaque, compact textures)
  - Uncompressed 32-bit RGBA (raw / uncompressed DDS container)

Conforms to Microsoft Direct3D 9 / 10 DDS specification:
  - 128-byte binary DDS header (magic 'DDS ' + 124-byte DDS_HEADER struct)
  - Pure Python + NumPy block compression with zero external C DLL dependencies.
"""

from __future__ import annotations

import struct
from typing import Any
import numpy as np
from PIL import Image

# DDS Header Constants
DDS_MAGIC = b"DDS "
DDS_HEADER_SIZE = 124  # Size of DDS_HEADER struct (excluding 4-byte magic)
DDS_PIXELFORMAT_SIZE = 32

# dwFlags (DDS_HEADER)
DDSD_CAPS = 0x00000001
DDSD_HEIGHT = 0x00000002
DDSD_WIDTH = 0x00000004
DDSD_PITCH = 0x00000008
DDSD_PIXELFORMAT = 0x00001000
DDSD_MIPMAPCOUNT = 0x00020000
DDSD_LINEARSIZE = 0x00080000
DDSD_DEPTH = 0x00800000

# dwCaps (DDS_HEADER)
DDSCAPS_COMPLEX = 0x00000008
DDSCAPS_TEXTURE = 0x00001000
DDSCAPS_MIPMAP = 0x00400000

# ddspf.dwFlags (DDS_PIXELFORMAT)
DDPF_ALPHAPIXELS = 0x00000001
DDPF_ALPHA = 0x00000002
DDPF_FOURCC = 0x00000004
DDPF_RGB = 0x00000040

# FourCC codes
FOURCC_DXT1 = b"DXT1"
FOURCC_DXT3 = b"DXT3"
FOURCC_DXT5 = b"DXT5"


def build_dds_header(
    width: int,
    height: int,
    fmt: str = "BC3",
    mip_count: int = 0,
) -> bytes:
    """Build a standard 128-byte binary DDS file header.

    Args:
        width: Texture width in pixels.
        height: Texture height in pixels.
        fmt: Compression format: "BC3" ("DXT5"), "BC1" ("DXT1"), or "RGBA".
        mip_count: Number of mipmap levels (0 or 1 for top-level only).

    Returns:
        128-byte header bytes (4-byte magic + 124-byte DDS_HEADER struct).
    """
    normalized_fmt = fmt.upper().replace("DDS_", "")

    if normalized_fmt in ("BC3", "DXT5"):
        flags = DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PIXELFORMAT | DDSD_LINEARSIZE
        block_w = max(1, (width + 3) // 4)
        block_h = max(1, (height + 3) // 4)
        pitch_or_linear = block_w * block_h * 16
        pf_flags = DDPF_FOURCC
        fourcc = struct.unpack("<I", FOURCC_DXT5)[0]
        rgb_bit_count = 0
        r_mask, g_mask, b_mask, a_mask = 0, 0, 0, 0
    elif normalized_fmt in ("BC1", "DXT1"):
        flags = DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PIXELFORMAT | DDSD_LINEARSIZE
        block_w = max(1, (width + 3) // 4)
        block_h = max(1, (height + 3) // 4)
        pitch_or_linear = block_w * block_h * 8
        pf_flags = DDPF_FOURCC
        fourcc = struct.unpack("<I", FOURCC_DXT1)[0]
        rgb_bit_count = 0
        r_mask, g_mask, b_mask, a_mask = 0, 0, 0, 0
    elif normalized_fmt in ("RGBA", "RAW_RGBA", "UNCOMPRESSED"):
        flags = DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PIXELFORMAT | DDSD_PITCH
        pitch_or_linear = width * 4
        pf_flags = DDPF_RGB | DDPF_ALPHAPIXELS
        fourcc = 0
        rgb_bit_count = 32
        # Direct3D standard 32-bit BGRA mask
        r_mask = 0x00FF0000
        g_mask = 0x0000FF00
        b_mask = 0x000000FF
        a_mask = 0xFF000000
    else:
        raise ValueError(f"Unsupported DDS format for header: {fmt}")

    caps = DDSCAPS_TEXTURE
    if mip_count > 1:
        flags |= DDSD_MIPMAPCOUNT
        caps |= DDSCAPS_MIPMAP | DDSCAPS_COMPLEX

    reserved1 = (0,) * 11
    caps2 = 0
    caps3 = 0
    caps4 = 0
    reserved2 = 0

    header = struct.pack(
        "<4s 7I 11I 4I 4I 5I",
        DDS_MAGIC,
        DDS_HEADER_SIZE,
        flags,
        height,
        width,
        pitch_or_linear,
        0,  # depth
        mip_count,
        *reserved1,
        DDS_PIXELFORMAT_SIZE,
        pf_flags,
        fourcc,
        rgb_bit_count,
        r_mask,
        g_mask,
        b_mask,
        a_mask,
        caps,
        caps2,
        caps3,
        caps4,
        reserved2,
    )
    return header


def decode_dds_header(data: bytes) -> dict[str, Any]:
    """Parse and validate a 128-byte DDS header.

    Args:
        data: At least 128 bytes from the start of a DDS file.

    Returns:
        Dictionary of header metadata including width, height, format, etc.
    """
    if len(data) < 128:
        raise ValueError(f"DDS header too short ({len(data)} bytes, expected at least 128)")

    magic = data[:4]
    if magic != DDS_MAGIC:
        raise ValueError(f"Invalid DDS magic: {magic!r}, expected {DDS_MAGIC!r}")

    unpacked = struct.unpack("<7I 11I 4I 4I 5I", data[4:128])
    (
        size,
        flags,
        height,
        width,
        pitch_or_linear,
        depth,
        mip_count,
        *_,
        pf_size,
        pf_flags,
        fourcc_int,
        rgb_bit_count,
        r_mask,
        g_mask,
        b_mask,
        a_mask,
        caps,
        caps2,
        caps3,
        caps4,
        reserved2,
    ) = unpacked

    fourcc_bytes = struct.pack("<I", fourcc_int)
    fourcc_str = (
        fourcc_bytes.decode("ascii", errors="replace")
        if (pf_flags & DDPF_FOURCC)
        else ""
    )

    fmt = "UNKNOWN"
    if pf_flags & DDPF_FOURCC:
        if fourcc_str == "DXT1":
            fmt = "BC1"
        elif fourcc_str == "DXT5":
            fmt = "BC3"
        else:
            fmt = fourcc_str
    elif pf_flags & DDPF_RGB:
        fmt = "RGBA" if (pf_flags & DDPF_ALPHAPIXELS) else "RGB"

    return {
        "magic": magic,
        "header_size": size,
        "flags": flags,
        "height": height,
        "width": width,
        "pitch_or_linear": pitch_or_linear,
        "depth": depth,
        "mip_count": mip_count,
        "pf_size": pf_size,
        "pf_flags": pf_flags,
        "fourcc": fourcc_str,
        "rgb_bit_count": rgb_bit_count,
        "r_mask": r_mask,
        "g_mask": g_mask,
        "b_mask": b_mask,
        "a_mask": a_mask,
        "caps": caps,
        "format": fmt,
    }


def rgb_to_565(r: int, g: int, b: int) -> int:
    """Pack 8-bit RGB components into a 16-bit RGB565 integer."""
    r5 = (int(r) >> 3) & 0x1F
    g6 = (int(g) >> 2) & 0x3F
    b5 = (int(b) >> 3) & 0x1F
    return (r5 << 11) | (g6 << 5) | b5


def rgb565_to_rgb(c: int) -> np.ndarray:
    """Unpack a 16-bit RGB565 integer into 8-bit RGB components (shape [3])."""
    c = int(c)
    r5 = (c >> 11) & 0x1F
    g6 = (c >> 5) & 0x3F
    b5 = c & 0x1F
    r8 = (r5 << 3) | (r5 >> 2)
    g8 = (g6 << 2) | (g6 >> 4)
    b8 = (b5 << 3) | (b5 >> 2)
    return np.array([r8, g8, b8], dtype=np.int32)


def _unpack_565_vectorized(c: np.ndarray) -> np.ndarray:
    """Unpack array of uint32/uint16 565 values into shape (N, 3) int32 RGB888."""
    r5 = (c >> 11) & 0x1F
    g6 = (c >> 5) & 0x3F
    b5 = c & 0x1F
    r8 = (r5 << 3) | (r5 >> 2)
    g8 = (g6 << 2) | (g6 >> 4)
    b8 = (b5 << 3) | (b5 >> 2)
    return np.stack([r8, g8, b8], axis=1).astype(np.int32)


def _pad_image_to_blocks(img_rgba: np.ndarray) -> tuple[np.ndarray, int, int]:
    """Pad image dimensions to multiples of 4 if needed."""
    h, w, c = img_rgba.shape
    pad_h = (4 - (h % 4)) % 4
    pad_w = (4 - (w % 4)) % 4
    if pad_h > 0 or pad_w > 0:
        padded = np.zeros((h + pad_h, w + pad_w, c), dtype=img_rgba.dtype)
        padded[:h, :w, :] = img_rgba
        return padded, h, w
    return img_rgba, h, w


def encode_bc3_blocks(img_rgba: np.ndarray) -> bytes:
    """Compress an RGBA image array into BC3 / DXT5 block data.

    Vectorized implementation using NumPy. Each 4x4 pixel block compresses
    to 16 bytes: 8 bytes for alpha + 8 bytes for RGB color.

    Args:
        img_rgba: (H, W, 4) uint8 RGBA array.

    Returns:
        Raw BC3 compressed bytes.
    """
    img_padded, orig_h, orig_w = _pad_image_to_blocks(img_rgba)
    h, w, _ = img_padded.shape
    bh, bw = h // 4, w // 4
    num_blocks = bh * bw

    # Reshape into 4x4 blocks: (bh, 4, bw, 4, 4) -> (num_blocks, 16, 4)
    blocks = (
        img_padded.reshape(bh, 4, bw, 4, 4)
        .transpose(0, 2, 1, 3, 4)
        .reshape(num_blocks, 16, 4)
    )

    # -------------------------------------------------------------
    # 1. Vectorized Alpha Block Encoding (8 bytes per block)
    # -------------------------------------------------------------
    alphas = blocks[:, :, 3].astype(np.int32)  # (num_blocks, 16)
    a_min = np.min(alphas, axis=1)  # (num_blocks,)
    a_max = np.max(alphas, axis=1)  # (num_blocks,)

    alpha_bytes = np.zeros((num_blocks, 8), dtype=np.uint8)
    same_alpha_mask = a_min == a_max
    diff_alpha_mask = ~same_alpha_mask

    if np.any(same_alpha_mask):
        alpha_bytes[same_alpha_mask, 0] = a_max[same_alpha_mask].astype(np.uint8)

    if np.any(diff_alpha_mask):
        a0 = a_max[diff_alpha_mask]  # (N,)
        a1 = a_min[diff_alpha_mask]  # (N,)

        # 8-alpha palette (a0 > a1 mode)
        p0 = a0
        p1 = a1
        p2 = (6 * a0 + 1 * a1) // 7
        p3 = (5 * a0 + 2 * a1) // 7
        p4 = (4 * a0 + 3 * a1) // 7
        p5 = (3 * a0 + 4 * a1) // 7
        p6 = (2 * a0 + 5 * a1) // 7
        p7 = (1 * a0 + 6 * a1) // 7
        palettes = np.stack([p0, p1, p2, p3, p4, p5, p6, p7], axis=1)  # (N, 8)

        diff_alphas = alphas[diff_alpha_mask]  # (N, 16)
        dist = np.abs(diff_alphas[:, :, None] - palettes[:, None, :])
        a_indices = np.argmin(dist, axis=2).astype(np.uint64)  # (N, 16)

        # Pack 16 3-bit indices into 48-bit little-endian
        shifts = np.arange(16, dtype=np.uint64) * 3
        packed_alpha = np.sum((a_indices & 7) << shifts, axis=1)  # (N,) uint64

        alpha_bytes[diff_alpha_mask, 0] = a0.astype(np.uint8)
        alpha_bytes[diff_alpha_mask, 1] = a1.astype(np.uint8)
        for b in range(6):
            alpha_bytes[diff_alpha_mask, 2 + b] = (
                (packed_alpha >> (8 * b)) & 0xFF
            ).astype(np.uint8)

    # -------------------------------------------------------------
    # 2. Vectorized Color Block Encoding (8 bytes per block)
    # -------------------------------------------------------------
    rgb = blocks[:, :, :3].astype(np.int32)  # (num_blocks, 16, 3)
    c_min = np.min(rgb, axis=1)  # (num_blocks, 3)
    c_max = np.max(rgb, axis=1)  # (num_blocks, 3)

    c0_16 = (
        (((c_max[:, 0] >> 3) & 0x1F) << 11)
        | (((c_max[:, 1] >> 2) & 0x3F) << 5)
        | ((c_max[:, 2] >> 3) & 0x1F)
    ).astype(np.uint32)
    c1_16 = (
        (((c_min[:, 0] >> 3) & 0x1F) << 11)
        | (((c_min[:, 1] >> 2) & 0x3F) << 5)
        | ((c_min[:, 2] >> 3) & 0x1F)
    ).astype(np.uint32)

    color_bytes = np.zeros((num_blocks, 8), dtype=np.uint8)
    same_color_mask = c0_16 == c1_16
    diff_color_mask = ~same_color_mask

    if np.any(same_color_mask):
        c_same = c0_16[same_color_mask]
        color_bytes[same_color_mask, 0] = (c_same & 0xFF).astype(np.uint8)
        color_bytes[same_color_mask, 1] = ((c_same >> 8) & 0xFF).astype(np.uint8)
        color_bytes[same_color_mask, 2] = color_bytes[same_color_mask, 0]
        color_bytes[same_color_mask, 3] = color_bytes[same_color_mask, 1]

    if np.any(diff_color_mask):
        c0 = c0_16[diff_color_mask].copy()
        c1 = c1_16[diff_color_mask].copy()
        swap = c0 < c1
        c0[swap], c1[swap] = c1[swap], c0[swap]

        rgb0 = _unpack_565_vectorized(c0)
        rgb1 = _unpack_565_vectorized(c1)
        rgb2 = (2 * rgb0 + rgb1) // 3
        rgb3 = (rgb0 + 2 * rgb1) // 3
        c_palettes = np.stack([rgb0, rgb1, rgb2, rgb3], axis=1)  # (N, 4, 3)

        diff_rgb = rgb[diff_color_mask]  # (N, 16, 3)
        c_dist = np.sum(
            (diff_rgb[:, :, None, :] - c_palettes[:, None, :, :]) ** 2, axis=3
        )
        c_indices = np.argmin(c_dist, axis=2).astype(np.uint32)
        c_shifts = np.arange(16, dtype=np.uint32) * 2
        packed_color = np.sum((c_indices & 3) << c_shifts, axis=1)

        color_bytes[diff_color_mask, 0] = (c0 & 0xFF).astype(np.uint8)
        color_bytes[diff_color_mask, 1] = ((c0 >> 8) & 0xFF).astype(np.uint8)
        color_bytes[diff_color_mask, 2] = (c1 & 0xFF).astype(np.uint8)
        color_bytes[diff_color_mask, 3] = ((c1 >> 8) & 0xFF).astype(np.uint8)
        color_bytes[diff_color_mask, 4] = (packed_color & 0xFF).astype(np.uint8)
        color_bytes[diff_color_mask, 5] = ((packed_color >> 8) & 0xFF).astype(np.uint8)
        color_bytes[diff_color_mask, 6] = ((packed_color >> 16) & 0xFF).astype(np.uint8)
        color_bytes[diff_color_mask, 7] = ((packed_color >> 24) & 0xFF).astype(np.uint8)

    # Interleave alpha and color blocks: 16 bytes per block
    dxt5_blocks = np.concatenate([alpha_bytes, color_bytes], axis=1)
    return dxt5_blocks.tobytes()


def encode_bc1_blocks(img_rgba: np.ndarray) -> bytes:
    """Compress an RGBA image array into BC1 / DXT1 block data.

    Supports 1-bit alpha punchthrough and 4-color opaque mode.
    Each 4x4 block compresses to 8 bytes.

    Args:
        img_rgba: (H, W, 4) uint8 RGBA array.

    Returns:
        Raw BC1 compressed bytes.
    """
    img_padded, orig_h, orig_w = _pad_image_to_blocks(img_rgba)
    h, w, _ = img_padded.shape
    bh, bw = h // 4, w // 4
    num_blocks = bh * bw

    blocks = (
        img_padded.reshape(bh, 4, bw, 4, 4)
        .transpose(0, 2, 1, 3, 4)
        .reshape(num_blocks, 16, 4)
    )

    alphas = blocks[:, :, 3]  # (num_blocks, 16)
    rgb = blocks[:, :, :3].astype(np.int32)
    opaque_mask = alphas >= 128
    opaque_counts = np.sum(opaque_mask, axis=1)

    color_bytes = np.zeros((num_blocks, 8), dtype=np.uint8)

    # Case 1: Fully transparent blocks
    fully_trans = opaque_counts == 0
    if np.any(fully_trans):
        color_bytes[fully_trans, 4:8] = 0xFF

    # Case 2: Fully opaque blocks (4-color mode)
    fully_opaque = opaque_counts == 16
    if np.any(fully_opaque):
        sub_rgb = rgb[fully_opaque]
        c_min = np.min(sub_rgb, axis=1)
        c_max = np.max(sub_rgb, axis=1)
        c0 = (
            (((c_max[:, 0] >> 3) & 0x1F) << 11)
            | (((c_max[:, 1] >> 2) & 0x3F) << 5)
            | ((c_max[:, 2] >> 3) & 0x1F)
        ).astype(np.uint32)
        c1 = (
            (((c_min[:, 0] >> 3) & 0x1F) << 11)
            | (((c_min[:, 1] >> 2) & 0x3F) << 5)
            | ((c_min[:, 2] >> 3) & 0x1F)
        ).astype(np.uint32)

        same = c0 == c1
        diff = ~same

        if np.any(same):
            c_same = c0[same]
            idx_sub = np.where(fully_opaque)[0][same]
            color_bytes[idx_sub, 0] = (c_same & 0xFF).astype(np.uint8)
            color_bytes[idx_sub, 1] = ((c_same >> 8) & 0xFF).astype(np.uint8)
            color_bytes[idx_sub, 2] = color_bytes[idx_sub, 0]
            color_bytes[idx_sub, 3] = color_bytes[idx_sub, 1]

        if np.any(diff):
            c0_d = c0[diff].copy()
            c1_d = c1[diff].copy()
            swap = c0_d < c1_d
            c0_d[swap], c1_d[swap] = c1_d[swap], c0_d[swap]

            rgb0 = _unpack_565_vectorized(c0_d)
            rgb1 = _unpack_565_vectorized(c1_d)
            rgb2 = (2 * rgb0 + rgb1) // 3
            rgb3 = (rgb0 + 2 * rgb1) // 3
            palettes = np.stack([rgb0, rgb1, rgb2, rgb3], axis=1)

            d_rgb = sub_rgb[diff]
            dist = np.sum((d_rgb[:, :, None, :] - palettes[:, None, :, :]) ** 2, axis=3)
            indices = np.argmin(dist, axis=2).astype(np.uint32)
            shifts = np.arange(16, dtype=np.uint32) * 2
            packed = np.sum((indices & 3) << shifts, axis=1)

            idx_sub = np.where(fully_opaque)[0][diff]
            color_bytes[idx_sub, 0] = (c0_d & 0xFF).astype(np.uint8)
            color_bytes[idx_sub, 1] = ((c0_d >> 8) & 0xFF).astype(np.uint8)
            color_bytes[idx_sub, 2] = (c1_d & 0xFF).astype(np.uint8)
            color_bytes[idx_sub, 3] = ((c1_d >> 8) & 0xFF).astype(np.uint8)
            color_bytes[idx_sub, 4] = (packed & 0xFF).astype(np.uint8)
            color_bytes[idx_sub, 5] = ((packed >> 8) & 0xFF).astype(np.uint8)
            color_bytes[idx_sub, 6] = ((packed >> 16) & 0xFF).astype(np.uint8)
            color_bytes[idx_sub, 7] = ((packed >> 24) & 0xFF).astype(np.uint8)

    # Case 3: Partial transparency blocks (1-bit alpha mode: c0 <= c1)
    partial = (~fully_trans) & (~fully_opaque)
    if np.any(partial):
        for idx_block in np.where(partial)[0]:
            b_rgb = rgb[idx_block]
            b_op = opaque_mask[idx_block]
            c_min = np.min(b_rgb[b_op], axis=0)
            c_max = np.max(b_rgb[b_op], axis=0)
            c0 = int(
                (((c_max[0] >> 3) & 0x1F) << 11)
                | (((c_max[1] >> 2) & 0x3F) << 5)
                | ((c_max[2] >> 3) & 0x1F)
            )
            c1 = int(
                (((c_min[0] >> 3) & 0x1F) << 11)
                | (((c_min[1] >> 2) & 0x3F) << 5)
                | ((c_min[2] >> 3) & 0x1F)
            )

            if c0 > c1:
                c0, c1 = c1, c0

            r0 = rgb565_to_rgb(c0)
            r1 = rgb565_to_rgb(c1)
            r2 = (r0 + r1) // 2
            pal = np.stack([r0, r1, r2], axis=0)
            dist = np.sum((b_rgb[:, None, :] - pal[None, :, :]) ** 2, axis=2)
            ind = np.argmin(dist, axis=1)
            ind[~b_op] = 3
            packed = sum(int(x & 3) << (2 * i) for i, x in enumerate(ind))

            color_bytes[idx_block, 0] = c0 & 0xFF
            color_bytes[idx_block, 1] = (c0 >> 8) & 0xFF
            color_bytes[idx_block, 2] = c1 & 0xFF
            color_bytes[idx_block, 3] = (c1 >> 8) & 0xFF
            color_bytes[idx_block, 4] = packed & 0xFF
            color_bytes[idx_block, 5] = (packed >> 8) & 0xFF
            color_bytes[idx_block, 6] = (packed >> 16) & 0xFF
            color_bytes[idx_block, 7] = (packed >> 24) & 0xFF

    return color_bytes.tobytes()


def encode_dds(image: Image.Image | np.ndarray, fmt: str = "DDS_BC3") -> bytes:
    """Encode an RGBA image into a complete binary DDS texture.

    Args:
        image: PIL Image or NumPy array [H, W, 4] uint8.
        fmt: Compression format:
            - "DDS_BC3", "BC3", "DXT5", "DDS_DXT5"
            - "DDS_BC1", "BC1", "DXT1", "DDS_DXT1"
            - "DDS_RGBA", "RGBA", "UNCOMPRESSED"

    Returns:
        Complete DDS file bytes (128-byte header + encoded pixel data).
    """
    if isinstance(image, Image.Image):
        if image.mode != "RGBA":
            image = image.convert("RGBA")
        arr = np.array(image, dtype=np.uint8)
        width, height = image.size
    elif isinstance(image, np.ndarray):
        arr = image.astype(np.uint8)
        height, width = arr.shape[:2]
        if arr.shape[2] != 4:
            raise ValueError(f"Expected 4-channel RGBA array, got shape {arr.shape}")
    else:
        raise TypeError(f"Unsupported image type: {type(image)}")

    normalized_fmt = fmt.upper().replace("DDS_", "")

    if normalized_fmt in ("BC3", "DXT5"):
        header = build_dds_header(width, height, fmt="BC3")
        payload = encode_bc3_blocks(arr)
    elif normalized_fmt in ("BC1", "DXT1"):
        header = build_dds_header(width, height, fmt="BC1")
        payload = encode_bc1_blocks(arr)
    elif normalized_fmt in ("RGBA", "RAW_RGBA", "UNCOMPRESSED"):
        header = build_dds_header(width, height, fmt="RGBA")
        # In DDS RGBA format, D3D pixel masks are BGRA
        bgra = arr[:, :, [2, 1, 0, 3]]
        payload = bgra.tobytes()
    else:
        raise ValueError(
            f"Unsupported format '{fmt}'. Choose from DDS_BC3, DDS_BC1, or RGBA."
        )

    return header + payload
