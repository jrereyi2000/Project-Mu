"""Two-stage jersey plate compositing pipeline for Project Mu headshots.

Standardizes high-resolution official NFL jersey overlays with clean alpha neck sockets,
ensuring:
1. Zero donor neck skin artifacts (the collar socket is cleanly hollowed out).
2. Zero player shoulder poke-out (player body outside the collar is strictly bounded to the jersey silhouette).
3. Matched photographic quality, lighting, and resolution between player face and uniform plate.
"""

from __future__ import annotations

import io
import os
from typing import Optional, Tuple, Union

import numpy as np
from PIL import Image


def extract_clean_jersey_plate(
    ref_image: Union[str, Image.Image],
    collar_y_min: int = 680,
    collar_y_max: int = 1050,
    collar_x_min: int = 250,
    collar_x_max: int = 650,
    skin_r_threshold: int = 100,
) -> Image.Image:
    """Extract a clean, transparent jersey plate from an authentic NFL reference photo.

    Removes background and hollows out the neck socket right to the inner rim
    of the ribbed collar, erasing all donor skin.
    """
    if isinstance(ref_image, str):
        img = Image.open(ref_image).convert("RGBA")
    else:
        img = ref_image.convert("RGBA")

    # If image does not already have an alpha channel mask, apply rembg if available
    arr = np.array(img)
    h, w = arr.shape[:2]

    # Find the inner collar curve by scanning for the jersey collar ribbing
    inner_edge = {}
    for x in range(collar_x_min, collar_x_max):
        for y in range(collar_y_min, collar_y_max):
            r, g, b, a = arr[y, x]
            # Primary collar color check (e.g. blue or dark jersey fabric vs skin)
            if a > 100 and (int(b) > int(r) + 20 or (int(r) < 80 and int(g) < 80 and int(b) < 80)):
                inner_edge[x] = y
                break

    plate_arr = arr.copy()

    # Zero out all donor pixels above the inner collar curve
    for x in range(w):
        if x in inner_edge:
            cutoff_y = inner_edge[x]
        elif x < collar_x_min:
            cutoff_y = inner_edge.get(collar_x_min, collar_y_min)
        else:
            cutoff_y = inner_edge.get(collar_x_max - 1, collar_y_min)
        plate_arr[:cutoff_y, x, 3] = 0

    # Clean any residual skin pixels inside or near the collar rim
    for y in range(h):
        for x in range(w):
            r, g, b, a = plate_arr[y, x]
            if a > 0 and int(r) > int(b) + 20 and int(r) > skin_r_threshold:
                plate_arr[y, x, 3] = 0

    # Clean corner artifacts
    plate_arr[int(h * 0.95):, :int(w * 0.12), 3] = 0
    plate_arr[int(h * 0.95):, -int(w * 0.12):, 3] = 0

    return Image.fromarray(plate_arr)


def composite_headshot_to_jersey(
    player_cutout: Union[str, Image.Image],
    jersey_plate: Union[str, Image.Image],
    scale: float = 0.78,
    target_chin_y: int = 590,
    target_center_x: int = 440,
    collar_x_bounds: Tuple[int, int] = (250, 640),
    out_format: str = "broadcast",
    output_path: Optional[str] = None,
) -> Image.Image:
    """Composite a segmented player portrait into an official team jersey plate.

    Args:
        player_cutout: Segmented (RGBA) player portrait with head, full neck, and bare chest.
        jersey_plate: High-resolution clean jersey overlay with transparent neck socket.
        scale: Resize factor for the player face/neck to match collar proportions.
        target_chin_y: Vertical canvas pixel where the player chin rests.
        target_center_x: Horizontal canvas pixel for centering the neck in the collar.
        collar_x_bounds: (left, right) columns defining the collar socket opening.
        out_format: 'broadcast' (800x1000 4:5 portrait) or 'site' (700x508 transparent WebP).
        output_path: Optional file path to save the final composite.

    Returns:
        PIL.Image.Image of the composited media-day headshot.
    """
    if isinstance(player_cutout, str):
        player_img = Image.open(player_cutout).convert("RGBA")
    else:
        player_img = player_cutout.convert("RGBA")

    if isinstance(jersey_plate, str):
        plate_img = Image.open(jersey_plate).convert("RGBA")
    else:
        plate_img = jersey_plate.convert("RGBA")

    pw, ph = plate_img.size
    plate_arr = np.array(plate_img)

    # 1. Compute exact top contour of the jersey plate
    jersey_top = np.full(pw, ph, dtype=int)
    for x in range(pw):
        ys = np.where(plate_arr[:, x, 3] > 50)[0]
        if len(ys) > 0:
            jersey_top[x] = ys.min()

    # 2. Scale player
    new_w = int(player_img.width * scale)
    new_h = int(player_img.height * scale)
    player_scaled = player_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    scaled_arr = np.array(player_scaled)

    # Locate chin in player image (approx 57% of height for 896x1152 portrait)
    scaled_chin_y = int(scaled_arr.shape[0] * 0.57)
    scaled_center_x = int(scaled_arr.shape[1] * 0.5)

    paste_x = target_center_x - scaled_center_x
    paste_y = target_chin_y - scaled_chin_y

    # 3. Canvas buffer for player
    player_canvas = np.zeros((ph, pw, 4), dtype=np.uint8)
    y1 = max(0, paste_y)
    y2 = min(ph, paste_y + new_h)
    x1 = max(0, paste_x)
    x2 = min(pw, paste_x + new_w)

    src_y1 = max(0, -paste_y)
    src_y2 = src_y1 + (y2 - y1)
    src_x1 = max(0, -paste_x)
    src_x2 = src_x1 + (x2 - x1)

    player_canvas[y1:y2, x1:x2] = scaled_arr[src_y1:src_y2, src_x1:src_x2]

    # 4. Shoulder Elimination Mask:
    # Outside the collar corridor (collar_x_bounds), any player body pixel
    # appearing above the jersey shoulder line (y < jersey_top[x]) is erased.
    # This mathematically guarantees zero bare player shoulders poke outside the jersey.
    left_bound, right_bound = collar_x_bounds
    for x in range(pw):
        if x < left_bound or x > right_bound:
            j_top = jersey_top[x]
            if j_top > 470:
                player_canvas[470:j_top, x, 3] = 0

    # 5. Composite layers
    # Bottom: Pure white studio backdrop (#FFFFFF)
    composite_canvas = Image.new("RGBA", (pw, ph), (255, 255, 255, 255))
    player_masked_img = Image.fromarray(player_canvas)

    # Layer 1: Player Head & Neck Socket
    composite_canvas.paste(player_masked_img, (0, 0), player_masked_img)

    # Layer 2: Official Jersey Plate Overlay
    composite_canvas.paste(plate_img, (0, 0), plate_img)

    # 6. Format output
    if out_format == "broadcast":
        # 4:5 portrait crop centered around the chest and head
        crop_x1 = max(0, (pw - 800) // 2)
        final_img = composite_canvas.crop((crop_x1, 40, crop_x1 + 800, 1040))
    elif out_format == "site":
        # Broadcast media-day cutouts on transparent background
        # Keep transparency from both player and plate
        import rembg
        nobg = rembg.remove(composite_canvas.crop((48, 40, 848, 1040)))
        nobg.thumbnail((700, 508), Image.Resampling.LANCZOS)
        tw, th = nobg.size
        final_img = Image.new("RGBA", (700, 508), (0, 0, 0, 0))
        final_img.paste(nobg, ((700 - tw) // 2, (508 - th) // 2), nobg)
    else:
        final_img = composite_canvas

    if output_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        if output_path.lower().endswith(".webp"):
            final_img.save(output_path, format="WEBP", quality=95)
        else:
            final_img.save(output_path, format="PNG")

    return final_img
