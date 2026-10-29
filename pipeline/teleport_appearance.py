# @AUDIT-STATUS: OBSOLETE_FRESH_SAVE (v2-teleport)
# @REPLACED-BY: NONE (fresh 2034 save, no teleport; AUDIT_LEDGER.md §3b)
"""
Appearance for the teleporter (docs/in_progress/PC_MIGRATION_PLAN.md §4.3 "Appearance"): gives each teleported donor a generic head
and body type close to the Project Mu player's AI headshot. Pure apart from headshot_seeds (one read-only query).

Where the headshot traits come from: headshots/headshot_appearance_generator.appearance() is seeded from the name and
team the headshot was drawn with, so the traits are recomputed by calling it with those same inputs:
  * the 1,499-player batch (headshots/make_headshot.build_batch_request) used 2035 rosters: players.player_name
    ('A. Manning'), teams.name ('Dolphins') and the 2035 player_seasons.position;
  * headshots/rookie_headshots.py (2036 classes on): COALESCE(full_name, player_name) and the rookie season's team and
    position;
  * anyone else (no headshot yet) falls back to player_name and the teleport season's team.
Skin tone, hair and facial hair depend only on name, team and position (the batch prompts in
data/headshots/batch_headshot_requests.jsonl reproduce exactly), so age and the stored height/weight don't matter here.

What the save holds (Madden 27 PC, checked on CAREER-GIANTS-AUTOSAVE):
  GenericHeadAssetName  string 'gen_7_B_G_005'   the generic head model; hair and facial hair are baked into it
  PLYR_GENERICHEAD      enum   '7_B_G_005'       the same head as an enum (NoHead on Madden-generated players)
  CharacterBodyType     enum   Thin / Standard / Muscular / Heavy (/ Freshman)
  PLYR_ASSETNAME        string 'LastFirst_<pgid>' for real players (whose scanned face is looked up by it, a PC
                               check), 'LastFirst_<pgid>_<n>' for Madden-generated ones
  CharacterVisuals      reference -> CharacterVisuals.RawData, a zstd (dictionary) compressed ISON blob: gear
                               loadouts and a `skinTone` 1-8 that tints the body. Updated via
                               pipeline/character_visuals.py and save_writer.mjs visual edits to match the head tone.
Head names read <skin tone 1-7>_<hair code>_<facial hair code>_<n>. The skin-tone digit is solid (it tracks
CharacterVisuals.skinTone; K/P heads are 96% digits 1-2, CB heads 84% digits 6-7); the hair and facial hair letters
are a reading of the names (N none, S stubble, G goatee, M moustache, B/BD beard; T twists/locs, H longer hair,
B/M short), unconfirmed until the heads are looked at on the PC, so they only narrow the choice and never empty it.
"""
import re
from collections import Counter

from headshots import headshot_appearance_generator as hag
from pipeline.character_visuals import HEAD_TO_BODY_SKIN_TONE

BATCH_SEASON = 2035             # rosters the original 1,499-headshot batch was drawn from
ROOKIE_HEADSHOTS_FROM = 2036    # first class drawn by headshots/rookie_headshots.py
HEAD_RX = re.compile(r"^gen_(\d)_([A-Z]+)_([A-Z]+)_(\d+)$")

# Headshot skin-tone bracket -> head skin-tone digits (1 lightest ... 7 darkest).
TONE_DIGITS = {"light": "12", "light-olive": "3", "medium tan": "4", "medium-deep brown": "56", "deep brown": "7"}
# Headshot facial hair -> head facial-hair codes.
FACIAL_CODES = {
    "clean-shaven": {"N"},
    "light stubble": {"S", "GS", "MS", "BMS"},
    "short trimmed beard": {"G", "M", "MG", "GM", "MB"},
    "full beard": {"B", "BD", "BM"},
}
# Headshot hair kind -> head hair-code letters (a head code like BMH carries several).
HAIR_LETTERS = {"locs": {"T"}, "long": {"H"}, "short": {"B", "M"}, "bald": {"B"}}
# headshot_appearance_generator.build() opens with one of these words.
BUILD_TO_BODY = {"lean": "Thin", "athletic": "Standard", "muscular": "Muscular", "very": "Heavy"}


def hair_kind(hair):
    """'shoulder-length locs (black hair), slightly receding hairline' -> 'locs' (locs / long / short / bald)."""
    style = hair.split(" (")[0].split(",")[0].strip()
    if style in hag.BALD_STYLES:
        return "bald"
    if any(w in style for w in ("locs", "braids", "twist", "cornrows")):
        return "locs"
    if any(w in style for w in ("shoulder-length", "long", "bun", "ear-length", "medium-length", "mop")):
        return "long"
    return "short"


def body_type(height_in, weight_lb):
    """Height/weight -> CharacterBodyType through the headshot's build line, or None when either is missing."""
    if not height_in or not weight_lb:
        return None
    return BUILD_TO_BODY[hag.build(height_in, weight_lb).split()[0]]


def head_pool(snapshot):
    """Generic heads in use in the save, parsed: [(asset, tone digit, hair letters, facial code)]. Only heads Madden
    already gives to players, so every one is a valid PLYR_GENERICHEAD and a loadable asset."""
    pool = set()
    for p in snapshot["players"]:
        m = HEAD_RX.match(p.get("appearance", {}).get("generic_head") or "")
        if m:
            pool.add((m.group(0), m.group(1), frozenset(m.group(2)), m.group(3)))
    return sorted(pool)


def traits(seed):
    """The headshot's traits from its seed {name, team, position}: {tone, hair, facial}."""
    _, meta = hag.appearance(seed["name"], seed["team"], seed["position"] or "WR", 74, 220, 25)
    return {"tone": meta["tone"], "hair": meta["hair"], "facial": meta["facial"]}


def pick_head(pool, t, seed):
    """(head asset, how many filters held: 3 tone+facial+hair, 2 tone+facial, 1 tone, 0 none). Deterministic: the
    choice among the candidates comes from the generator's RNG on the headshot seed."""
    digits = TONE_DIGITS[t["tone"]]
    tone = [h for h in pool if h[1] in digits]
    facial = [h for h in tone if h[3] in FACIAL_CODES[t["facial"]]]
    hair = [h for h in facial if h[2] & HAIR_LETTERS[hair_kind(t["hair"])]]
    for level, cands in ((3, hair), (2, facial), (1, tone), (0, pool)):
        if cands:
            return hag.make_rng(seed["name"], seed["team"], "madden-head").choice(cands)[0], level
    return None, 0


def asset_name(first, last, pgid):
    """Madden's real-player asset pattern ('AbneyIIKeith_16233') for the new name, so the donor's scanned face is no
    longer looked up (PC check) and the generic head shows."""
    return f"{re.sub(r'[^A-Za-z]', '', last)}{re.sub(r'[^A-Za-z]', '', first)}"[:30] + f"_{pgid}"


def appearance_fields(pool, seed, first, last, pgid, height_in, weight_lb):
    """(extra save_writer `set` fields, info) for one teleported player."""
    t = traits(seed)
    head, level = pick_head(pool, t, seed)
    set_ = {}
    body_skin_tone = None
    if head:
        set_["GenericHeadAssetName"] = head
        set_["PLYR_GENERICHEAD"] = head[len("gen_"):]
        set_["PLYR_ASSETNAME"] = asset_name(first, last, pgid)
        m = HEAD_RX.match(head)
        if m:
            tone_digit = int(m.group(1))
            body_skin_tone = HEAD_TO_BODY_SKIN_TONE.get(tone_digit)
    body = body_type(height_in, weight_lb)
    if body:
        set_["CharacterBodyType"] = body
    return set_, {"tone": t["tone"], "facial": t["facial"], "hair": hair_kind(t["hair"]), "head": head,
                  "level": level, "body": body, "body_skin_tone": body_skin_tone}


def summarize(infos, seeds_source):
    """Report block: trait and head distributions, so a run shows the heads follow the headshots."""
    digit = Counter(int(HEAD_RX.match(i["head"]).group(1)) for i in infos if i["head"])
    body_tones = Counter(i["body_skin_tone"] for i in infos if i.get("body_skin_tone"))
    return {
        "players": len(infos),
        "seed_source": dict(seeds_source),
        "headshot_tone": dict(Counter(i["tone"] for i in infos)),
        "head_tone_digit": dict(sorted(digit.items())),
        "body_skin_tone": dict(sorted(body_tones.items())),
        "headshot_facial": dict(Counter(i["facial"] for i in infos)),
        "headshot_hair": dict(Counter(i["hair"] for i in infos)),
        "filters_held": dict(Counter({3: "tone+facial+hair", 2: "tone+facial", 1: "tone", 0: "none"}[i["level"]]
                                     for i in infos)),
        "distinct_heads": len({i["head"] for i in infos}),
        "body_type": dict(Counter(i["body"] for i in infos)),
    }


# --------------------------------------------------------------------- load ----
def headshot_seeds(cur, season):
    """{player_id: {name, team, position, source}} for every player on a `season` roster (see the module docstring)."""
    cur.execute("""
        SELECT ps.player_id, ps.season, ps.years_of_experience, p.player_name,
               COALESCE(NULLIF(TRIM(p.full_name), ''), p.player_name), t.name, ps.position
        FROM player_seasons ps JOIN players p ON p.id = ps.player_id JOIN teams t ON t.id = ps.team_id
        WHERE ps.player_id IN (SELECT player_id FROM player_seasons WHERE season = %s)
          AND (ps.season IN (%s, %s) OR (ps.season BETWEEN %s AND %s AND ps.years_of_experience = 0))""",
                (season, BATCH_SEASON, season, ROOKIE_HEADSHOTS_FROM, season))
    rows = {}
    for pid, s, yoe, short, full, team, pos in cur.fetchall():
        rows.setdefault(pid, []).append((s, yoe, short, full, team, pos))
    seeds = {}
    for pid, rs in rows.items():
        rookie = [r for r in rs if r[1] == 0 and r[0] >= ROOKIE_HEADSHOTS_FROM]
        batch = [r for r in rs if r[0] == BATCH_SEASON]
        current = [r for r in rs if r[0] == season]
        if rookie:
            _, _, _, full, team, pos = min(rookie)
            seeds[pid] = {"name": full, "team": team, "position": pos, "source": "rookie_headshots"}
        elif batch:
            _, _, short, _, team, pos = batch[0]
            seeds[pid] = {"name": short, "team": team, "position": pos, "source": "batch"}
        elif current:
            _, _, short, _, team, pos = current[0]
            seeds[pid] = {"name": short, "team": team, "position": pos, "source": "no headshot"}
    return seeds
