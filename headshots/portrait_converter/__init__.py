"""Project Mu Standalone Portrait Converter.

Provides headshot normalization, auto-framing, pure Python DDS encoding (BC3/BC1/RGBA),
and CLI tools for converting WebP media-day cutouts to Madden 27 in-game portraits.
"""

from headshots.portrait_converter.converter import (
    convert_headshot_file,
    convert_headshots_batch,
    frame_headshot,
    to_portrait,
)
from headshots.portrait_converter.dds import (
    build_dds_header,
    decode_dds_header,
    encode_bc1_blocks,
    encode_bc3_blocks,
    encode_dds,
)

__all__ = [
    "to_portrait",
    "convert_headshot_file",
    "convert_headshots_batch",
    "frame_headshot",
    "encode_dds",
    "encode_bc1_blocks",
    "encode_bc3_blocks",
    "build_dds_header",
    "decode_dds_header",
]
