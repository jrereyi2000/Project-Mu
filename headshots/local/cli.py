"""Standalone command-line interface for local headshot generation.

Usage:
    python -m headshots.local.cli --prompt "..." --out test.png [--backend mock|diffusers|comfyui] [--seed 42]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import List, Optional

from headshots.local.generator import generate_headshot


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m headshots.local.cli",
        description="Generate photorealistic sports player headshots locally using Project Mu.",
    )
    parser.add_argument(
        "--prompt",
        type=str,
        required=True,
        help="Text prompt describing the player, jersey, lighting, and style.",
    )
    parser.add_argument(
        "--out",
        type=str,
        default="headshot.png",
        help="Output PNG file path (default: headshot.png).",
    )
    parser.add_argument(
        "--backend",
        type=str,
        choices=["mock", "diffusers", "comfyui"],
        default=None,
        help="Backend to use (default: mock, or env LOCAL_HEADSHOT_BACKEND).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Integer seed for reproducible generation.",
    )
    parser.add_argument(
        "--size",
        type=int,
        default=1024,
        help="Image size in pixels (default: 1024).",
    )
    parser.add_argument(
        "--ref-image",
        type=str,
        default=None,
        help="Optional path to reference face image for IP-Adapter consistency.",
    )
    parser.add_argument(
        "--controlnet",
        action="store_true",
        help="Enable ControlNet pose/depth conditioning for uniform head framing.",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="JSON string or path to JSON file with additional backend config.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    logger = logging.getLogger("headshots.local.cli")

    # Load ref image bytes if provided
    ref_bytes = None
    if args.ref_image:
        if not os.path.exists(args.ref_image):
            logger.error("Reference image not found: %s", args.ref_image)
            return 1
        with open(args.ref_image, "rb") as f:
            ref_bytes = f.read()

    # Parse config if provided
    extra_config = {}
    if args.config:
        if os.path.exists(args.config):
            with open(args.config, "r", encoding="utf-8") as f:
                extra_config = json.load(f)
        else:
            try:
                extra_config = json.loads(args.config)
            except json.JSONDecodeError as e:
                logger.error("Invalid JSON provided in --config: %s", e)
                return 1

    if args.controlnet:
        extra_config["controlnet"] = True

    try:
        logger.info(
            "Generating headshot (backend=%s, size=%d, seed=%s)...",
            args.backend or "default",
            args.size,
            args.seed,
        )
        png_bytes = generate_headshot(
            prompt=args.prompt,
            ref_image=ref_bytes,
            seed=args.seed,
            size=args.size,
            backend=args.backend,
            config=extra_config,
        )

        out_dir = os.path.dirname(os.path.abspath(args.out))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

        with open(args.out, "wb") as f:
            f.write(png_bytes)

        logger.info("Successfully generated headshot: %s (%d bytes)", args.out, len(png_bytes))
        print(f"Saved headshot ({len(png_bytes)} bytes) to {args.out}")
        return 0

    except Exception as e:
        logger.error("Headshot generation failed: %s", e, exc_info=args.verbose)
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
