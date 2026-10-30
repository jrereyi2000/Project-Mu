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
from typing import Optional, Sequence, Tuple, Union

import numpy as np
from PIL import Image


# Pixel defaults below were tuned on a 896x1200 reference plate; when a parameter is left as
# None it is scaled from that reference to the plate's actual size.
REF_W, REF_H = 896, 1200


def _scaled(value: Optional[int], ref_value: int, size: int, ref_size: int) -> int:
    v = round(ref_value * size / ref_size) if value is None else int(value)
    return max(0, min(v, size))


def _skin_mask(rgb: np.ndarray, fabric_colors: Sequence[Tuple[int, int, int]], fabric_tolerance: float) -> np.ndarray:
    """True where a pixel's chroma falls in the human skin range (YCbCr) and it isn't one of the jersey's
    own colours. Chroma alone can't tell skin from brown, burgundy or old-gold fabric, so the plate's
    fabric colours are excluded explicitly."""
    rgb = rgb.astype(np.float32)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    cb = 128 - 0.168736 * r - 0.331264 * g + 0.5 * b
    cr = 128 + 0.5 * r - 0.418688 * g - 0.081312 * b
    skin = (cb >= 77) & (cb <= 127) & (cr >= 133) & (cr <= 173)
    for color in fabric_colors:
        dist = np.sqrt(((rgb - np.array(color, dtype=np.float32)) ** 2).sum(axis=-1))
        skin &= dist > fabric_tolerance
    return skin


def _sample_fabric_color(arr: np.ndarray, y0: int, y1: int, x0: int, x1: int) -> Optional[Tuple[int, int, int]]:
    """Median colour of the opaque torso fabric in the given box, or None if it has no opaque pixels."""
    box = arr[y0:y1, x0:x1]
    opaque = box[box[..., 3] > 100][:, :3]
    if len(opaque) == 0:
        return None
    return tuple(int(c) for c in np.median(opaque, axis=0))


def extract_clean_jersey_plate(
    ref_image: Union[str, Image.Image],
    collar_y_min: Optional[int] = None,
    collar_y_max: Optional[int] = None,
    collar_x_min: Optional[int] = None,
    collar_x_max: Optional[int] = None,
    fabric_colors: Optional[Sequence[Tuple[int, int, int]]] = None,
    fabric_tolerance: float = 40.0,
    rim_band: Optional[int] = None,
) -> Image.Image:
    """Extract a clean, transparent jersey plate from an authentic NFL reference photo.

    Hollows out the neck socket right to the inner rim of the collar, erasing donor skin.

    Args:
        ref_image: Jersey reference photo (path or image), background already transparent or plain.
        collar_y_min / collar_y_max / collar_x_min / collar_x_max: box searched for the collar rim;
            default scaled from (680, 1050, 250, 650) on a 896x1200 plate.
        fabric_colors: Jersey colours never treated as skin. Default: the torso colour sampled just
            below the collar box. Add trim colours (e.g. a gold collar stripe) if they read as skin.
        fabric_tolerance: RGB distance within which a pixel counts as one of fabric_colors.
        rim_band: Rows below the collar rim cleaned of leftover donor skin; default 3% of the height.
    """
    if isinstance(ref_image, str):
        img = Image.open(ref_image).convert("RGBA")
    else:
        img = ref_image.convert("RGBA")

    arr = np.array(img)
    h, w = arr.shape[:2]
    y_min = _scaled(collar_y_min, 680, h, REF_H)
    y_max = max(y_min, _scaled(collar_y_max, 1050, h, REF_H))
    x_min = _scaled(collar_x_min, 250, w, REF_W)
    x_max = max(x_min, _scaled(collar_x_max, 650, w, REF_W))
    band = _scaled(rim_band, 36, h, REF_H)

    if fabric_colors is None:
        sampled = _sample_fabric_color(arr, y_max, min(h, y_max + max(1, h // 10)), x_min, x_max)
        fabric_colors = [sampled] if sampled is not None else []
    skin = _skin_mask(arr[..., :3], fabric_colors, fabric_tolerance)
    opaque = arr[..., 3] > 100

    # An opaque studio backdrop isn't the collar: skip pixels matching the top-left corner when it is opaque
    background = np.zeros((h, w), dtype=bool)
    if opaque[0, 0]:
        corner = arr[0, 0, :3].astype(np.float32)
        background = np.sqrt(((arr[..., :3].astype(np.float32) - corner) ** 2).sum(axis=-1)) < 30

    # Inner collar rim per column: the first opaque pixel in the box that is neither skin nor backdrop
    candidate = opaque & ~skin & ~background
    inner_edge = {}
    for x in range(x_min, x_max):
        ys = np.nonzero(candidate[y_min:y_max, x])[0]
        if len(ys):
            inner_edge[x] = y_min + int(ys[0])

    plate_arr = arr.copy()

    # Zero out all donor pixels above the inner collar curve
    for x in range(w):
        if x in inner_edge:
            cutoff_y = inner_edge[x]
        elif x < x_min:
            cutoff_y = inner_edge.get(x_min, y_min)
        else:
            cutoff_y = inner_edge.get(x_max - 1, y_min)
        plate_arr[:cutoff_y, x, 3] = 0

        # Donor skin left just under the rim (only there: elsewhere a skin-coloured pixel is fabric)
        if x_min <= x < x_max:
            rim = slice(cutoff_y, min(h, cutoff_y + band))
            plate_arr[rim, x, 3] = np.where(skin[rim, x], 0, plate_arr[rim, x, 3])

    # Clean corner artifacts
    plate_arr[int(h * 0.95):, :int(w * 0.12), 3] = 0
    plate_arr[int(h * 0.95):, -int(w * 0.12):, 3] = 0

    return Image.fromarray(plate_arr)


def composite_headshot_to_jersey(
    player_cutout: Union[str, Image.Image],
    jersey_plate: Union[str, Image.Image],
    scale: float = 0.78,
    target_chin_y: Optional[int] = None,
    target_center_x: Optional[int] = None,
    collar_x_bounds: Optional[Tuple[int, int]] = None,
    out_format: str = "broadcast",
    output_path: Optional[str] = None,
) -> Image.Image:
    """Composite a segmented player portrait into an official team jersey plate.

    Args:
        player_cutout: Segmented (RGBA) player portrait with head, full neck, and bare chest.
        jersey_plate: High-resolution clean jersey overlay with transparent neck socket.
        scale: Resize factor for the player face/neck to match collar proportions.
        target_chin_y: Vertical canvas pixel where the player chin rests (default 590 on a 896x1200 plate,
            scaled to the plate's size).
        target_center_x: Horizontal canvas pixel for centering the neck in the collar (default 440, scaled).
        collar_x_bounds: (left, right) columns defining the collar socket opening (default (305, 595), scaled).
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

    target_chin_y = min(_scaled(target_chin_y, 590, ph, REF_H), ph - 1)
    target_center_x = _scaled(target_center_x, 440, pw, REF_W)
    if collar_x_bounds is None:
        collar_x_bounds = (_scaled(None, 305, pw, REF_W), _scaled(None, 595, pw, REF_W))
    else:
        collar_x_bounds = (_scaled(collar_x_bounds[0], 0, pw, REF_W), _scaled(collar_x_bounds[1], 0, pw, REF_W))

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

    # 4. Anatomical Neck Corridor & Shoulder Silhouette Mask:
    # Generates a smooth anatomical funnel that transitions from the jaw/chin
    # directly into the jersey collar apexes, eliminating all bare player trapezius
    # and shoulder flares above the jersey shoulder contour.
    left_apex_x, right_apex_x = collar_x_bounds

    # Measure neck width at chin level or fallback to standard anatomical proportions
    chin_row_alpha = player_canvas[target_chin_y, :, 3]
    chin_xs = np.where(chin_row_alpha > 50)[0]
    if len(chin_xs) > 0:
        chin_neck_left = float(max(chin_xs.min(), target_center_x - int(pw * 0.14)))
        chin_neck_right = float(min(chin_xs.max(), target_center_x + int(pw * 0.15)))
    else:
        chin_neck_left = float(target_center_x - int(pw * 0.14))
        chin_neck_right = float(target_center_x + int(pw * 0.15))

    # Determine apex y level on jersey plate: minimum jersey_top within apex bounds
    apex_slice = jersey_top[left_apex_x:right_apex_x]
    apex_y = (
        int(apex_slice.min())
        if len(apex_slice) > 0 and apex_slice.min() < ph
        else target_chin_y + 98
    )

    # 4a. Above the jersey shoulder line (y < jersey_top[x]), strictly enforce the natural neck corridor:
    for y in range(target_chin_y, ph):
        if y <= apex_y:
            t = (y - target_chin_y) / max(1.0, float(apex_y - target_chin_y))
            c_left = chin_neck_left - t * (chin_neck_left - left_apex_x)
            c_right = chin_neck_right + t * (right_apex_x - chin_neck_right)
        else:
            c_left = float(left_apex_x)
            c_right = float(right_apex_x)

        for x in range(pw):
            jt = jersey_top[x]
            if y < jt:
                # Background region above jersey: eliminate trap/shoulder protrusions
                if x < c_left - 3.0:
                    player_canvas[y, x, 3] = 0
                elif x < c_left:
                    feather = (x - (c_left - 3.0)) / 3.0
                    player_canvas[y, x, 3] = int(player_canvas[y, x, 3] * feather)
                elif x > c_right + 3.0:
                    player_canvas[y, x, 3] = 0
                elif x > c_right:
                    feather = ((c_right + 3.0) - x) / 3.0
                    player_canvas[y, x, 3] = int(player_canvas[y, x, 3] * feather)

    # 4b. Under the jersey:
    # Zero out outer shoulders outside the collar socket region below chin level
    # so player deltoids do not bleed through outer armholes or edges
    outer_left = max(0, left_apex_x - 25)
    outer_right = min(pw, right_apex_x + 25)
    for x in range(pw):
        if x < outer_left or x > outer_right:
            player_canvas[target_chin_y:, x, 3] = 0

    # 5. Composite layers
    # Bottom: Pure white studio backdrop (#FFFFFF)
    composite_canvas = Image.new("RGBA", (pw, ph), (255, 255, 255, 255))
    player_masked_img = Image.fromarray(player_canvas)

    # Layer 1: Player Head & Neck Socket
    composite_canvas.paste(player_masked_img, (0, 0), player_masked_img)

    # Layer 2: Official Jersey Plate Overlay
    composite_canvas.paste(plate_img, (0, 0), plate_img)

    # 6. Format output
    # Crop box: 800x1000 from y=40 on a 896x1200 plate, scaled to the plate's size
    crop_w = _scaled(None, 800, pw, REF_W)
    crop_y1 = _scaled(None, 40, ph, REF_H)
    crop_y2 = min(ph, crop_y1 + _scaled(None, 1000, ph, REF_H))
    crop_x1 = max(0, (pw - crop_w) // 2)
    crop_box = (crop_x1, crop_y1, crop_x1 + crop_w, crop_y2)
    if out_format == "broadcast":
        # 4:5 portrait crop centered around the chest and head, always delivered at 800x1000
        final_img = composite_canvas.crop(crop_box)
        if final_img.size != (800, 1000):
            final_img = final_img.resize((800, 1000), Image.Resampling.LANCZOS)
    elif out_format == "site":
        # Broadcast media-day cutouts on transparent background
        # Keep transparency from both player and plate
        import rembg
        nobg = rembg.remove(composite_canvas.crop(crop_box))
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
