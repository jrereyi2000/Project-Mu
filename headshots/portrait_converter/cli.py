"""Command Line Interface for Project Mu Portrait Converter.

Supports single-file headshot conversion and directory-level batch processing:
  Single file:
    python -m headshots.portrait_converter.cli input.webp --out output.dds [--width 512] [--height 512] [--fmt BC3]
  Batch directory:
    python -m headshots.portrait_converter.cli --batch-dir static/players/ --out-dir portraits/ --limit 5
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Sequence

from headshots.portrait_converter.converter import (
    convert_headshot_file,
    convert_headshots_batch,
)


def build_parser() -> argparse.ArgumentParser:
    """Construct CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="python -m headshots.portrait_converter.cli",
        description="Convert transparent player headshot cutouts into Madden-ready DDS / texture portraits.",
    )
    parser.add_argument(
        "input",
        nargs="?",
        default=None,
        help="Input image file (e.g. static/players/1.webp). Omit when using --batch-dir.",
    )
    parser.add_argument(
        "--out",
        "-o",
        default=None,
        help="Output destination path (e.g. output.dds). Defaults to <input>.dds in single mode.",
    )
    parser.add_argument(
        "--batch-dir",
        default=None,
        help="Directory of source WebP headshots for batch conversion (e.g. static/players/).",
    )
    parser.add_argument(
        "--out-dir",
        default="portraits",
        help="Output directory for batch conversion (default: 'portraits/').",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of files to process in batch mode.",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=512,
        help="Destination portrait width in pixels (default: 512).",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=512,
        help="Destination portrait height in pixels (default: 512).",
    )
    parser.add_argument(
        "--fmt",
        default="DDS_BC3",
        help="Texture format: DDS_BC3 / BC3, DDS_BC1 / BC1, or RGBA (default: DDS_BC3).",
    )
    parser.add_argument(
        "--crop",
        nargs=4,
        type=int,
        metavar=("XMIN", "YMIN", "XMAX", "YMAX"),
        default=None,
        help="Optional manual crop rectangle: XMIN YMIN XMAX YMAX.",
    )
    parser.add_argument(
        "--headroom",
        type=float,
        default=0.05,
        help="Fraction of height for top headroom margin (default: 0.05).",
    )
    parser.add_argument(
        "--bottom-padding",
        type=float,
        default=0.0,
        help="Fraction of height for bottom chin/chest padding (default: 0.0).",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress informational stdout output.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI execution entrypoint."""
    parser = build_parser()
    args = parser.parse_args(argv)

    crop_box = tuple(args.crop) if args.crop else None

    # Handle Batch Directory Mode
    if args.batch_dir:
        batch_path = Path(args.batch_dir)
        if not batch_path.is_dir():
            print(f"Error: Batch directory '{args.batch_dir}' does not exist.", file=sys.stderr)
            return 1

        if not args.quiet:
            limit_str = f" (limit {args.limit})" if args.limit else ""
            print(
                f"[Portrait Converter] Batch converting from '{args.batch_dir}' -> '{args.out_dir}'{limit_str} "
                f"[{args.width}x{args.height}, {args.fmt}]..."
            )

        t0 = time.perf_counter()
        results = convert_headshots_batch(
            input_dir=args.batch_dir,
            output_dir=args.out_dir,
            width=args.width,
            height=args.height,
            fmt=args.fmt,
            limit=args.limit,
        )
        elapsed = time.perf_counter() - t0

        if not args.quiet:
            print(
                f"[Portrait Converter] Converted {len(results)} portraits in {elapsed:.2f}s "
                f"({len(results)/max(elapsed, 0.001):.1f} portraits/s)."
            )
        return 0

    # Handle Single File Mode
    if args.input:
        in_path = Path(args.input)
        if not in_path.is_file():
            print(f"Error: Input file '{args.input}' not found.", file=sys.stderr)
            return 1

        if args.out:
            out_path = Path(args.out)
        else:
            ext = ".rgba" if args.fmt.upper() == "RGBA" else ".dds"
            out_path = in_path.with_suffix(ext)

        if not args.quiet:
            print(
                f"[Portrait Converter] Converting '{in_path}' -> '{out_path}' "
                f"[{args.width}x{args.height}, {args.fmt}]..."
            )

        t0 = time.perf_counter()
        dest = convert_headshot_file(
            input_path=in_path,
            output_path=out_path,
            width=args.width,
            height=args.height,
            fmt=args.fmt,
            crop_box=crop_box,
            headroom_ratio=args.headroom,
            bottom_padding_ratio=args.bottom_padding,
        )
        elapsed = time.perf_counter() - t0

        if not args.quiet:
            print(f"[Portrait Converter] Success -> {dest} ({dest.stat().st_size:,} bytes, {elapsed*1000:.1f}ms)")
        return 0

    parser.print_help(file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
