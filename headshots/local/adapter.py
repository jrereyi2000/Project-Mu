"""Integration adapter for routing headshot generation to local backends.

Provides hooks for `headshots/make_headshot.py` and `headshots/rookie_headshots.py`
to route image generation locally when `USE_LOCAL_HEADSHOTS=1` or `LOCAL_HEADSHOTS=1`.
"""

from __future__ import annotations

import functools
import io
import logging
import os
import sys
from typing import Any, Callable, Dict, Optional, Tuple

from headshots.local.generator import generate_headshot

logger = logging.getLogger("headshots.local.adapter")

ENV_VARS = ("USE_LOCAL_HEADSHOTS", "LOCAL_HEADSHOTS")
TRUE_VALUES = {"1", "true", "yes", "on", "t"}

_ORIGINAL_MAKE_HEADSHOT: Optional[Callable] = None
_ORIGINAL_ROOKIE_GENERATE: Optional[Callable] = None


def is_local_headshots_enabled() -> bool:
    """Check whether local headshot generation is enabled via environment variables."""
    for var in ENV_VARS:
        val = os.environ.get(var, "").strip().lower()
        if val in TRUE_VALUES:
            return True
    return False


def get_local_generator_if_enabled() -> Optional[Callable[..., bytes]]:
    """Return local generator callable if enabled by environment, otherwise None."""
    if not is_local_headshots_enabled():
        return None

    backend_name = os.environ.get("LOCAL_HEADSHOT_BACKEND")

    @functools.wraps(generate_headshot)
    def _local_gen(
        prompt: str,
        ref_image: Optional[bytes] = None,
        seed: Optional[int] = None,
        size: int = 1024,
        backend: Optional[str] = backend_name,
        config: Optional[Dict[str, Any]] = None,
    ) -> bytes:
        return generate_headshot(
            prompt=prompt,
            ref_image=ref_image,
            seed=seed,
            size=size,
            backend=backend or backend_name,
            config=config,
        )

    return _local_gen


class _MockInlineData:
    def __init__(self, data: bytes):
        self.data = data


class _MockPart:
    def __init__(self, data: bytes):
        self.inline_data = _MockInlineData(data)


class _MockContent:
    def __init__(self, parts: list):
        self.parts = parts


class _MockCandidate:
    def __init__(self, content: _MockContent):
        self.content = content
        self.finish_reason = "STOP"


class _MockGenAIResponse:
    def __init__(self, png_bytes: bytes):
        self.candidates = [_MockCandidate(_MockContent([_MockPart(png_bytes)]))]


def patch_headshot_generator(
    target_modules: Optional[list] = None,
    force: bool = False,
    backend: Optional[str] = None,
) -> bool:
    """Patch `make_headshot` and `rookie_headshots` to route generation locally.

    Args:
        target_modules: Optional list of module objects to patch. If omitted,
                        attempts to import and patch `headshots.make_headshot` and
                        `headshots.rookie_headshots`.
        force: If True, patch regardless of environment variable setting.
        backend: Optional backend override to pass to `generate_headshot`.

    Returns:
        bool: True if patched, False otherwise.
    """
    global _ORIGINAL_MAKE_HEADSHOT, _ORIGINAL_ROOKIE_GENERATE

    if not force and not is_local_headshots_enabled():
        return False

    # 1. Patch headshots.make_headshot if available
    try:
        import headshots.make_headshot as m_make
        if _ORIGINAL_MAKE_HEADSHOT is None:
            _ORIGINAL_MAKE_HEADSHOT = m_make.make_headshot

        def patched_make_headshot(
            name: str,
            team: str,
            pos: str,
            height: str,
            weight: Any,
            age: Any,
            ref_image_bytes: Optional[bytes] = None,
            out: Optional[str] = None,
        ) -> str:
            line, _ = m_make.appearance(
                name, team, pos, m_make.parse_height(height), float(weight), int(age)
            )
            v = m_make.parse_line(line)
            c = m_make.team_colors(team)
            v.update(
                PRIMARY=c["primary"]["name"],
                PRIMARY_HEX=c["primary"]["hex"],
                SECONDARY=c["secondary"]["name"],
                SECONDARY_HEX=c["secondary"]["hex"],
            )

            prompt = m_make.IMAGE_PROMPT
            for k, val in v.items():
                prompt = prompt.replace("{" + k + "}", val)

            # Generate PNG bytes via local engine
            png_bytes = generate_headshot(
                prompt=prompt,
                ref_image=ref_image_bytes,
                backend=backend or os.environ.get("LOCAL_HEADSHOT_BACKEND"),
            )

            out_path = out or f"{name.replace(' ', '_')}.png"
            with open(out_path, "wb") as f:
                f.write(png_bytes)
            return out_path

        m_make.make_headshot = patched_make_headshot
        logger.info("Patched headshots.make_headshot with local generator.")
    except Exception as e:
        logger.warning("Could not patch headshots.make_headshot: %s", e)

    # 2. Patch headshots.rookie_headshots if available
    try:
        import headshots.rookie_headshots as r_make
        if hasattr(r_make, "generate_player_headshot"):
            if _ORIGINAL_ROOKIE_GENERATE is None:
                _ORIGINAL_ROOKIE_GENERATE = r_make.generate_player_headshot

            orig_func = _ORIGINAL_ROOKIE_GENERATE

            def patched_generate_player_headshot(player: Dict[str, Any], static_dir: str, backup_dir: str) -> str:
                # If target webp already exists, keep standard behavior
                target_webp = os.path.join(static_dir, f"{player['id']}.webp")
                if os.path.exists(target_webp) and os.path.getsize(target_webp) > 0:
                    return target_webp

                line, _ = r_make.appearance(
                    player["full_name"],
                    player["team"],
                    player["position"],
                    r_make.parse_height(player["height"]),
                    float(player["weight"]),
                    int(player["age"]),
                )
                v = r_make.parse_line(line)
                c = r_make.team_colors(player["team"])
                v.update(
                    PRIMARY=c["primary"]["name"],
                    PRIMARY_HEX=c["primary"]["hex"],
                    SECONDARY=c["secondary"]["name"],
                    SECONDARY_HEX=c["secondary"]["hex"],
                )
                prompt = r_make.IMAGE_PROMPT
                for k, val in v.items():
                    prompt = prompt.replace("{" + k + "}", val)

                # Route through local generator
                png_bytes = generate_headshot(
                    prompt=prompt,
                    backend=backend or os.environ.get("LOCAL_HEADSHOT_BACKEND"),
                )

                # Process with rembg and save webp
                from PIL import Image
                import rembg

                raw_im = Image.open(io.BytesIO(png_bytes))
                session = r_make.get_thread_rembg_session()
                nobg = rembg.remove(raw_im, session=session)

                if backup_dir:
                    os.makedirs(backup_dir, exist_ok=True)
                    backup_path = os.path.join(backup_dir, f"{player['id']}.webp")
                    nobg.save(backup_path, format="WEBP", quality=90, method=4)

                os.makedirs(static_dir, exist_ok=True)
                final_img = nobg.resize((700, 508), Image.Resampling.LANCZOS)
                final_img.save(target_webp, format="WEBP", quality=90, method=4)
                return target_webp

            r_make.generate_player_headshot = patched_generate_player_headshot
            logger.info("Patched headshots.rookie_headshots.generate_player_headshot with local generator.")
    except Exception as e:
        logger.warning("Could not patch headshots.rookie_headshots: %s", e)

    return True


def unpatch_headshot_generator() -> None:
    """Restore original generator functions in headshot modules."""
    global _ORIGINAL_MAKE_HEADSHOT, _ORIGINAL_ROOKIE_GENERATE

    if _ORIGINAL_MAKE_HEADSHOT is not None:
        try:
            import headshots.make_headshot as m_make
            m_make.make_headshot = _ORIGINAL_MAKE_HEADSHOT
        except Exception:
            pass
        _ORIGINAL_MAKE_HEADSHOT = None

    if _ORIGINAL_ROOKIE_GENERATE is not None:
        try:
            import headshots.rookie_headshots as r_make
            r_make.generate_player_headshot = _ORIGINAL_ROOKIE_GENERATE
        except Exception:
            pass
        _ORIGINAL_ROOKIE_GENERATE = None
