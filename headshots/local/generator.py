"""Core generator interface for local headshot generation in Project Mu."""

from __future__ import annotations

import io
from typing import Any, Dict, Optional, Union

from PIL import Image

from headshots.local.backends import HeadshotBackend, get_backend

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def generate_headshot(
    prompt: str,
    ref_image: Optional[bytes] = None,
    seed: Optional[int] = None,
    size: int = 1024,
    backend: Optional[Union[str, HeadshotBackend]] = None,
    config: Optional[Dict[str, Any]] = None,
) -> bytes:
    """Generate a high-quality player headshot locally.

    Conforms to the standalone interface specified in PC_MIGRATION_PLAN §7.7
    and PRE_PC_WORK_2 Item A1:
    `generate_headshot(prompt: str, ref_image: bytes | None, seed: int, size: int = 1024) -> bytes`

    Args:
        prompt: Text description of the player appearance, jersey, and lighting.
        ref_image: Optional reference image PNG/JPEG bytes for face identity preservation.
        seed: Optional integer seed for reproducible generation.
        size: Dimension of output portrait in pixels (default 1024).
        backend: Backend name ("mock", "diffusers", "comfyui") or a HeadshotBackend instance.
                 If omitted, reads from env LOCAL_HEADSHOT_BACKEND or defaults to "mock".
        config: Extra backend configuration dictionary.

    Returns:
        bytes: Valid PNG image data.

    Raises:
        ValueError: If prompt is empty or output data is not valid PNG.
        RuntimeError: If image generation fails.
    """
    if not prompt or not prompt.strip():
        raise ValueError("Prompt cannot be empty.")

    backend_instance = get_backend(backend=backend, config=config)
    image_bytes = backend_instance.generate(
        prompt=prompt,
        ref_image=ref_image,
        seed=seed,
        size=size,
        config=config,
    )

    if not isinstance(image_bytes, bytes) or len(image_bytes) == 0:
        raise RuntimeError("Backend returned empty image data.")

    # Validate PNG magic bytes or convert if backend returned another valid format
    if not image_bytes.startswith(PNG_MAGIC):
        try:
            pil_img = Image.open(io.BytesIO(image_bytes))
            buf = io.BytesIO()
            pil_img.save(buf, format="PNG")
            image_bytes = buf.getvalue()
        except Exception as e:
            raise ValueError(f"Output is not a valid PNG image: {e}") from e

    return image_bytes
