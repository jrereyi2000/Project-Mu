import json
import base64
from google import genai
from google.genai import types
from headshots.headshot_appearance_generator import appearance, parse_height

def get_client():
    global client
    if client is None:
        client = genai.Client()
    return client

client = None
IMAGE_MODEL = "gemini-nano-banana-2.1"   # verify current image model name
TEXT_MODEL = "gemini-3.8-flash"

COLOR_PROMPT = """Return ONLY JSON, no prose, no markdown fences:
{"primary": {"name": "...", "hex": "#......"},
 "secondary": {"name": "...", "hex": "#......"},
 "tertiary": {"name": "...", "hex": "#......"}}
These are the official HOME jersey colors of the NFL team: {TEAM}.
If you cannot verify a color, use the closest standard team color."""
IMAGE_PROMPT = """Create a photorealistic professional sports-league media-day headshot of a
fictional {POSITION} for the {TEAM}. The subject is {FACE}. He is {AGE_CUE},
with {SKIN_TONE} skin, {HAIR}, and {FACIAL_HAIR}. Build: {BUILD}. Natural skin
texture with visible pores and realistic imperfections, not airbrushed and not
beautified. Relaxed, confident expression with a slight closed-mouth smile,
looking straight into the camera, head level, shoulders squared with a very
slight turn.

He wears the official {TEAM} home jersey in {PRIMARY} ({PRIMARY_HEX}) with
{SECONDARY} ({SECONDARY_HEX}) and white shoulder stripes and a ribbed v-neck
collar, with realistic mesh texture and natural fabric folds. No helmet, hat,
chains, or other accessories.

Framing: head-and-shoulders crop cut off just below the collar, head filling
about 55-60% of the frame height, centered, with a small amount of headroom,
ladnscape orientation. The background is a perfectly seamless pure white
(#FFFFFF) studio backdrop with no gradient, vignette, floor, or wall visible.

Lighting: soft two-light studio setup with a large softbox key light from
camera-left at 45 degrees, a fill light from camera-right at about half power,
and a subtle rim light for hair separation from the white background. Gentle
shadow under the chin, no harsh highlights.

Photographic style: shot on a full-frame camera with an 85mm lens at f/5.6,
sharp focus on the eyes, neutral color grading, accurate skin tones, commercial
sports-media quality.

Do not include any text, watermark, caption, name overlay, extra logos,
scenery, hands, or full body. It must look like a real photograph, not an
illustration or 3D render."""
STYLE_REF = """The attached image is a style reference only. Match its exact lighting, framing,
crop, white background, and photographic style. Do NOT copy its face, hair,
skin tone, or build; use only the subject described above, only change the player details and jersey"""

_color_cache = {
    "Cardinals": {"primary": {"name": "Cardinal Red", "hex": "#97233F"}, "secondary": {"name": "White", "hex": "#FFFFFF"}},
    "Falcons": {"primary": {"name": "Black", "hex": "#000000"}, "secondary": {"name": "Red", "hex": "#A71930"}},
    "Ravens": {"primary": {"name": "Purple", "hex": "#241773"}, "secondary": {"name": "Black", "hex": "#000000"}},
    "Bills": {"primary": {"name": "Royal Blue", "hex": "#00338D"}, "secondary": {"name": "Red", "hex": "#C60C30"}},
    "Panthers": {"primary": {"name": "Process Blue", "hex": "#0085CA"}, "secondary": {"name": "Black", "hex": "#101820"}},
    "Bears": {"primary": {"name": "Navy Blue", "hex": "#0B162A"}, "secondary": {"name": "Orange", "hex": "#C83803"}},
    "Bengals": {"primary": {"name": "Black", "hex": "#000000"}, "secondary": {"name": "Orange", "hex": "#FB4F14"}},
    "Browns": {"primary": {"name": "Brown", "hex": "#311D00"}, "secondary": {"name": "Orange", "hex": "#FF3C00"}},
    "Cowboys": {"primary": {"name": "Navy Blue", "hex": "#003594"}, "secondary": {"name": "Silver", "hex": "#041E42"}},
    "Broncos": {"primary": {"name": "Orange", "hex": "#FB4F14"}, "secondary": {"name": "Navy Blue", "hex": "#002244"}},
    "Lions": {"primary": {"name": "Honolulu Blue", "hex": "#0076B6"}, "secondary": {"name": "Silver", "hex": "#B0B7BC"}},
    "Packers": {"primary": {"name": "Dark Green", "hex": "#203731"}, "secondary": {"name": "Gold", "hex": "#FFB612"}},
    "Texans": {"primary": {"name": "Deep Steel Blue", "hex": "#03202F"}, "secondary": {"name": "Battle Red", "hex": "#A71930"}},
    "Colts": {"primary": {"name": "Speedway Blue", "hex": "#002C5F"}, "secondary": {"name": "White", "hex": "#FFFFFF"}},
    "Jaguars": {"primary": {"name": "Teal", "hex": "#006778"}, "secondary": {"name": "Black", "hex": "#101820"}},
    "Chiefs": {"primary": {"name": "Red", "hex": "#E31837"}, "secondary": {"name": "Gold", "hex": "#FFB81C"}},
    "Raiders": {"primary": {"name": "Black", "hex": "#000000"}, "secondary": {"name": "Silver", "hex": "#A5ACAF"}},
    "Chargers": {"primary": {"name": "Powder Blue", "hex": "#0080C6"}, "secondary": {"name": "Sunshine Gold", "hex": "#FFC20E"}},
    "Rams": {"primary": {"name": "Royal Blue", "hex": "#003594"}, "secondary": {"name": "Sol", "hex": "#FFA300"}},
    "Dolphins": {"primary": {"name": "Aqua", "hex": "#008E97"}, "secondary": {"name": "Orange", "hex": "#FC4C02"}},
    "Vikings": {"primary": {"name": "Purple", "hex": "#4F2683"}, "secondary": {"name": "Gold", "hex": "#FFC62F"}},
    "Patriots": {"primary": {"name": "Nautical Blue", "hex": "#002244"}, "secondary": {"name": "Red", "hex": "#C60C30"}},
    "Saints": {"primary": {"name": "Black", "hex": "#101820"}, "secondary": {"name": "Old Gold", "hex": "#D3BC8D"}},
    "Giants": {"primary": {"name": "Royal Blue", "hex": "#0B2265"}, "secondary": {"name": "Red", "hex": "#A71930"}},
    "Jets": {"primary": {"name": "Gotham Green", "hex": "#125740"}, "secondary": {"name": "Spotlight White", "hex": "#FFFFFF"}},
    "Eagles": {"primary": {"name": "Midnight Green", "hex": "#004C54"}, "secondary": {"name": "Silver", "hex": "#A5ACAF"}},
    "Steelers": {"primary": {"name": "Black", "hex": "#101820"}, "secondary": {"name": "Gold", "hex": "#FFB612"}},
    "49ers": {"primary": {"name": "Red", "hex": "#AA0000"}, "secondary": {"name": "Gold", "hex": "#B3995D"}},
    "Seahawks": {"primary": {"name": "College Navy", "hex": "#002244"}, "secondary": {"name": "Action Green", "hex": "#69BE28"}},
    "Buccaneers": {"primary": {"name": "Buccaneer Red", "hex": "#D50A0A"}, "secondary": {"name": "Pewter", "hex": "#34302B"}},
    "Titans": {"primary": {"name": "Navy Blue", "hex": "#0C2340"}, "secondary": {"name": "Titans Blue", "hex": "#4B92DB"}},
    "Commanders": {"primary": {"name": "Burgundy", "hex": "#5A1414"}, "secondary": {"name": "Gold", "hex": "#FFB612"}}
}
def team_colors(team):
    if team not in _color_cache:
        r = get_client().models.generate_content(
            model=TEXT_MODEL,
            contents=COLOR_PROMPT.replace("{TEAM}", team),
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        _color_cache[team] = json.loads(r.text)
    return _color_cache[team]

def parse_line(line):
    f = [p.strip() for p in line.split(" | ")]
    return {
        "TEAM": f[1], "POSITION": f[2],
        "SKIN_TONE": f[3].removeprefix("skin tone: "),
        "HAIR": f[4].removeprefix("hair: "),
        "FACIAL_HAIR": f[5].removeprefix("facial hair: "),
        "BUILD": f[6], "AGE_CUE": f[7],
        "FACE": f[8].removeprefix("face: "),
    }

def make_headshot(name, team, pos, height, weight, age, ref_image_bytes=None, out=None):
    line, _ = appearance(name, team, pos, parse_height(height), float(weight), int(age))
    v = parse_line(line)
    c = team_colors(team)
    v.update(PRIMARY=c["primary"]["name"], PRIMARY_HEX=c["primary"]["hex"],
             SECONDARY=c["secondary"]["name"], SECONDARY_HEX=c["secondary"]["hex"])

    prompt = IMAGE_PROMPT
    for k, val in v.items():
        prompt = prompt.replace("{" + k + "}", val)

    contents = [prompt]
    if ref_image_bytes:
        contents = [prompt + "\n\n" + STYLE_REF,
                    types.Part.from_bytes(data=ref_image_bytes, mime_type="image/png")]

    r = get_client().models.generate_content(
        model=IMAGE_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(aspect_ratio="4:3"),
        ),
    )
    for part in r.candidates[0].content.parts:
        if part.inline_data:
            path = out or f"{name.replace(' ', '_')}.png"
            open(path, "wb").write(part.inline_data.data)
            return path
    raise RuntimeError(f"No image returned for {name}: {r.candidates[0].finish_reason}")

def build_batch_request(name, team, pos, height, weight, age, ref_image_bytes=None, key_override=None):
    # 1. Generate appearance and colors (retained from original script)
    line, _ = appearance(name, team, pos, parse_height(height), float(weight), int(age))
    v = parse_line(line)
    c = team_colors(team)
    v.update(PRIMARY=c["primary"]["name"], PRIMARY_HEX=c["primary"]["hex"],
             SECONDARY=c["secondary"]["name"], SECONDARY_HEX=c["secondary"]["hex"])

    # 2. Build and inject variables into the prompt
    prompt = IMAGE_PROMPT
    for k, val in v.items():
        prompt = prompt.replace("{" + k + "}", val)

    # 3. Construct the API 'parts' array
    parts = [{"text": prompt}]
    
    # 4. Append the Base64 reference image if provided
    if ref_image_bytes:
        parts = [
            {"text": prompt + "\n\n" + STYLE_REF},
            {
                "inlineData": {
                    "mimeType": "image/png",
                    "data": base64.b64encode(ref_image_bytes).decode("utf-8")
                }
            }
        ]

    # 5. Build the final JSON object matching the Gemini Batch API schema
    batch_key = key_override if key_override else f"{name.replace(' ', '_')}_{team}"
    request_obj = {
        "key": batch_key,
        "request": {
            "contents": [
                {
                    "role": "user",
                    "parts": parts
                }
            ],
            "generationConfig": {
                "responseModalities": ["IMAGE"],
                "imageConfig": {"aspectRatio": "3:4"}
            }
        }
    }
    
    return json.dumps(request_obj)

if __name__ == "__main__":
    import argparse
    import sys
    import os

    parser = argparse.ArgumentParser(description="Generate a player headshot using Gemini API.")
    parser.add_argument("name", help="Player name (e.g., 'Jake Miller')")
    parser.add_argument("team", help="NFL Team (e.g., 'Patriots')")
    parser.add_argument("position", help="Position (e.g., 'WR')")
    parser.add_argument("height", help="Height (e.g., \"6'1\")")
    parser.add_argument("weight", type=float, help="Weight in lbs (e.g., 205)")
    parser.add_argument("age", type=int, help="Age (e.g., 24)")
    parser.add_argument("--ref", help="Path to reference style image (optional)", default=None)
    parser.add_argument("--out", help="Output filename (optional)", default=None)

    args = parser.parse_args()

    # Ensure the GEMINI_API_KEY environment variable is set
    if not os.environ.get("GEMINI_API_KEY"):
        print("Error: GEMINI_API_KEY environment variable not set.")
        sys.exit(1)

    ref_bytes = None
    if args.ref:
        try:
            with open(args.ref, "rb") as f:
                ref_bytes = f.read()
        except FileNotFoundError:
            print(f"Error: Could not find reference image at {args.ref}")
            sys.exit(1)

    print(f"Generating headshot for {args.name} ({args.position} - {args.team})...")
    
    try:
        output_path = make_headshot(
            name=args.name, 
            team=args.team, 
            pos=args.position, 
            height=args.height, 
            weight=args.weight, 
            age=args.age, 
            ref_image_bytes=ref_bytes, 
            out=args.out
        )
        print(f"Success! Image saved to: {output_path}")
    except Exception as e:
        print(f"Failed to generate headshot: {e}")
        sys.exit(1)