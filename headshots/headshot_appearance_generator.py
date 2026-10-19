#!/usr/bin/env python3
"""
Appearance line generator for fictional NFL player headshots.

Samples skin tone, hair, facial hair, build, age cue, and attractiveness
from weighted tables, seeded by name+team so a given player is always
reproducible. Paste the output line into the Gemini Gem.

Usage:
  Single player:
    python headshot_appearance.py "Jake Miller,Patriots,WR,6'1,205,24"

  Batch from CSV (columns: name,team,position,height,weight,age):
    python headshot_appearance.py --csv roster.csv --out lines.txt

  Re-roll only the face / only the look:
    --hair-version 2     re-rolls hair only
    --face-version 2     re-rolls attractiveness features only
    --version 2          re-rolls skin tone and facial hair (hair and face
                         are on their own streams, so combine flags as needed)
"""

import argparse
import csv
import hashlib
import random
import re
import sys
from collections import Counter

# ----------------------------------------------------------------------------
# Tables (all weights are rough estimates: tune to whatever roster data you trust)
# ----------------------------------------------------------------------------

TONES = ["light", "light-olive", "medium tan", "medium-deep brown", "deep brown"]

# Skin-tone weights by position, in the same order as TONES
TONE_W = {
    "QB": [42, 8, 15, 20, 15],
    "RB": [12, 6, 12, 35, 35],
    "WR": [12, 6, 12, 35, 35],
    "TE": [45, 10, 10, 20, 15],
    "OL": [35, 10, 17, 20, 18],
    "DL": [15, 8, 22, 27, 28],
    "LB": [22, 8, 15, 28, 27],
    "CB": [5, 3, 10, 37, 45],
    "S":  [14, 6, 12, 34, 34],
    "K":  [75, 10, 8, 4, 3],
    "P":  [75, 10, 8, 4, 3],
}

POS_ALIASES = {
    "HB": "RB", "FB": "RB",
    "T": "OL", "G": "OL", "C": "OL", "OT": "OL", "OG": "OL", "IOL": "OL",
    "DE": "DL", "DT": "DL", "NT": "DL", "EDGE": "DL",
    "OLB": "LB", "ILB": "LB", "MLB": "LB",
    "FS": "S", "SS": "S", "DB": "CB", "NB": "CB",
    "PK": "K", "LS": "OL",
}

# Hair is built in layers: texture (by skin tone) -> style (by texture)
# -> natural color (by skin tone) -> optional dye -> optional receding hairline.
HAIR_TEXTURES = ["straight", "wavy", "curly", "coily"]

# Texture weights per skin tone index (same order as TONES)
TEXTURE_W = [
    [55, 30, 13, 2],    # light
    [45, 30, 22, 3],    # light-olive
    [30, 25, 30, 15],   # medium tan
    [8, 12, 25, 55],    # medium-deep brown
    [3, 5, 12, 80],     # deep brown
]

HAIR_STYLES = {
    "straight": [
        "buzz cut", "military high-and-tight", "short crew cut",
        "short side part", "short textured crop", "ivy league cut",
        "short slicked-back hair", "medium-length swept-back hair",
        "undercut with longer top swept to the side", "flat top",
        "messy short hair with a forward fringe",
        "ear-length hair tucked behind the ears",
        "shoulder-length straight hair", "shoulder-length hair pulled into a low bun",
        "closely cropped hair", "short spiky hair", "shaved bald head",
    ],
    "wavy": [
        "short wavy hair", "medium-length wavy hair swept back",
        "messy medium-length waves", "tapered sides with a wavy top",
        "slicked-back waves", "shoulder-length wavy hair",
        "long wavy hair tucked behind the ears", "short wavy side part",
        "buzz cut", "low fade with wavy top", "wavy hair pushed off the forehead",
        "shaved bald head",
    ],
    "curly": [
        "short tight curls", "medium-length curly top with faded sides",
        "curly mop", "shoulder-length curls", "tapered curly hair",
        "curly undercut", "short curly crop", "loose curls swept back",
        "low fade with defined curls on top", "buzz cut", "clean-shaved bald head",
    ],
    "coily": [
        "low fade with short curls", "short natural afro", "medium afro",
        "tapered afro", "high-top fade", "low-cut waves", "short twists",
        "twist-out", "faded sides with twisted top", "cornrows",
        "medium-length braids", "box braids", "shoulder-length locs",
        "short locs", "locs pulled back", "freeform locs", "buzz cut",
        "closely cropped hair", "clean-shaved bald head",
    ],
}

BALD_STYLES = {"shaved bald head", "clean-shaved bald head"}

HAIR_COLORS = ["black", "dark brown", "medium brown", "light brown",
               "dirty blond", "blond", "auburn"]
COLOR_W = [
    [6, 28, 28, 12, 13, 8, 5],     # light
    [20, 40, 25, 8, 4, 1, 2],      # light-olive
    [40, 40, 15, 3, 1, 0, 1],      # medium tan
    [75, 22, 3, 0, 0, 0, 0],       # medium-deep brown
    [92, 8, 0, 0, 0, 0, 0],        # deep brown
]
DYE_CHANCE = 0.03
DYE_COLORS = ["bleached blond", "platinum blond"]

FACIAL = [
    ("clean-shaven", 45),
    ("light stubble", 20),
    ("short trimmed beard", 25),
    ("full beard", 10),
]
FACIAL_QB_K_P = [65, 15, 15, 5]  # these positions skew clean-shaven

# Attractiveness: normal distribution, most players land mid-range.
# Raise ATTR_MEAN for a better-looking league, lower it for a plainer one.
ATTR_MEAN, ATTR_SD = 5.5, 1.7

# (below average, average, above average) per feature
ATTR_FEATURES = {
    "symmetry": ("noticeably asymmetric face",
                 "mildly asymmetric, natural face",
                 "very symmetrical face"),
    "jaw":      ("soft, undefined jawline",
                 "ordinary jawline",
                 "strong, well-defined jawline"),
    "nose":     ("broad or slightly crooked nose",
                 "average nose",
                 "straight, proportionate nose"),
    "skin":     ("uneven skin with blemishes or faint acne scarring",
                 "normal skin with minor imperfections",
                 "clear, even skin"),
    "eyes":     ("small, deep-set eyes",
                 "average eyes",
                 "bright, well-proportioned eyes"),
}


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def make_rng(*parts):
    """Deterministic RNG from any strings."""
    key = "|".join(str(p) for p in parts)
    return random.Random(int(hashlib.sha256(key.encode()).hexdigest(), 16))


def normalize_pos(pos):
    pos = pos.strip().upper()
    return POS_ALIASES.get(pos, pos)


def parse_height(s):
    """Accepts 6'2, 6-2, 6'2", 6 2, or total inches (74)."""
    s = str(s).strip()
    m = re.match(r"(\d+)\D+(\d+)", s)
    return int(m[1]) * 12 + int(m[2]) if m else int(s)


def build(h_in, w_lb):
    bmi = 703 * w_lb / (h_in ** 2)
    if bmi < 26:
        return "lean build, slim neck, narrow shoulders, defined jaw"
    if bmi < 30:
        return "athletic build, moderately thick neck, broad shoulders"
    if bmi < 35:
        return ("muscular stocky build, thick neck, very broad shoulders, "
                "fuller face")
    return ("very large build, very thick neck, extremely wide shoulders, "
            "full round face with soft jawline")


def age_cue(a):
    if a < 23:
        return "youthful face, early 20s"
    if a < 27:
        return "mid-20s"
    if a < 31:
        return "late 20s, mature face"
    if a < 35:
        return "early 30s, faint fine lines around the eyes"
    return ("mid-to-late 30s, visible fine lines, "
            "a few grey flecks in the hair")


def attractiveness(name, team, face_version=1):
    """Separate RNG stream so adding/changing this never re-rolls other traits."""
    rng = make_rng(name, team, "attr", face_version)
    score = max(1.0, min(10.0, rng.gauss(ATTR_MEAN, ATTR_SD)))
    parts = []
    for opts in ATTR_FEATURES.values():
        s = score + rng.gauss(0, 1.3)  # per-feature jitter
        parts.append(opts[0] if s < 4.5 else opts[1] if s < 7 else opts[2])
    if score < 4.5:
        anchor = "an ordinary, plain-looking man, not model-like"
    elif score < 7:
        anchor = "an average-looking man, approachable, not model-like"
    elif score < 10:
        anchor = "a notably handsome man"
    else:
        anchor = "an unbelievably, model like, handsome man"
    return round(score, 1), f"{anchor}; " + ", ".join(parts)


def pick_hair(name, team, ti, age, hair_version=1):
    """Own RNG stream so re-rolling hair never touches tone or face."""
    rng = make_rng(name, team, "hair", hair_version)
    texture = rng.choices(HAIR_TEXTURES, weights=TEXTURE_W[ti])[0]
    style = rng.choice(HAIR_STYLES[texture])
    color = rng.choices(HAIR_COLORS, weights=COLOR_W[ti])[0]
    if rng.random() < DYE_CHANCE:
        color = rng.choice(DYE_COLORS)
    receding_roll = rng.random()

    if style in BALD_STYLES:
        return style
    out = f"{style} ({color} hair)"
    if age >= 27 and receding_roll < min(0.40, 0.04 * (age - 26)):
        out += ", slightly receding hairline"

    return out


# ----------------------------------------------------------------------------
# Main appearance builder
# ----------------------------------------------------------------------------

def appearance(name, team, pos, height_in, weight_lb, age,
               version=1, face_version=1, hair_version=1):
    """Returns (line, meta) where meta holds values for summary stats."""
    pos = normalize_pos(pos)
    rng = make_rng(name, team, version)  # skin tone, hair, facial hair

    tone_w = TONE_W.get(pos, TONE_W["WR"])
    ti = rng.choices(range(5), weights=tone_w)[0]

    hair = pick_hair(name, team, ti, age, hair_version)
    fw = FACIAL_QB_K_P if pos in ("QB", "K", "P") else [x[1] for x in FACIAL]
    face = make_rng(name, team, "facial", version).choices(
        [x[0] for x in FACIAL], weights=fw)[0]

    score, look = attractiveness(name, team, face_version)

    line = (
        f"{name} | {team} | {pos} | skin tone: {TONES[ti]} | hair: {hair} | "
        f"facial hair: {face} | {build(height_in, weight_lb)} | "
        f"{age_cue(age)} | face: {look} | attr_score: {score}"
    )
    meta = {"pos": pos, "tone": TONES[ti], "hair": hair,
            "facial": face, "score": score}
    return line, meta


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def summarize(metas):
    n = len(metas)
    print(f"\n--- Summary ({n} players) ---", file=sys.stderr)

    tones = Counter(m["tone"] for m in metas)
    print("Skin tone:", file=sys.stderr)
    for t in TONES:
        print(f"  {t:<18}{tones[t]:>4}  ({100 * tones[t] / n:.0f}%)",
              file=sys.stderr)

    facial = Counter(m["facial"] for m in metas)
    print("Facial hair:", file=sys.stderr)
    for f, c in facial.most_common():
        print(f"  {f:<22}{c:>4}  ({100 * c / n:.0f}%)", file=sys.stderr)

    print(f"Unique hair looks: {len({m['hair'] for m in metas})} of {n}",
          file=sys.stderr)

    bins = Counter(min(int(m["score"]), 9) for m in metas)
    print("Attractiveness (score histogram):", file=sys.stderr)
    for b in range(1, 10):
        label = f"{b}-{b + 1}" if b < 9 else "9-10"
        print(f"  {label:<6}{'#' * bins[b]} {bins[b]}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("player", nargs="?",
                    help='"Name,Team,Position,Height,Weight,Age" (quote it)')
    ap.add_argument("--csv", help="roster CSV with columns "
                                  "name,team,position,height,weight,age")
    ap.add_argument("--out", help="write lines to this file instead of stdout")
    ap.add_argument("--version", type=int, default=1,
                    help="re-roll skin tone/hair/facial hair")
    ap.add_argument("--face-version", type=int, default=1,
                    help="re-roll attractiveness features only")
    ap.add_argument("--hair-version", type=int, default=1,
                    help="re-roll hair only")
    args = ap.parse_args()

    rows = []
    if args.csv:
        with open(args.csv, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                r = {k.strip().lower(): v.strip() for k, v in r.items()}
                rows.append((r["name"], r["team"], r["position"],
                             r["height"], r["weight"], r["age"]))
    elif args.player:
        parts = [p.strip() for p in next(csv.reader([args.player]))]
        if len(parts) != 6:
            ap.error("player must be: Name,Team,Position,Height,Weight,Age "
                     "(quote heights like \"6'1\" inside the CSV-style string)")
        rows.append(tuple(parts))
    else:
        ap.error("provide a player string or --csv")

    lines, metas = [], []
    for name, team, pos, h, w, age in rows:
        line, meta = appearance(name, team, pos, parse_height(h),
                                float(w), int(age),
                                version=args.version,
                                face_version=args.face_version,
                                hair_version=args.hair_version)
        lines.append(line)
        metas.append(meta)

    output = "\n".join(lines)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(output + "\n")
        print(f"Wrote {len(lines)} lines to {args.out}", file=sys.stderr)
    else:
        print(output)

    if len(metas) > 1:
        summarize(metas)


if __name__ == "__main__":
    main()
