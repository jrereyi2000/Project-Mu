"""
The Madden coach record for an invented coach or coordinator (COACH_SAVE_WRITES_PLAN §2.4, U1).

`build_profile` is pure and seeded. Every constant below is copied from Spike A
(`docs/reference/MADDEN_COACH_RECORDS.md`): the enum members real coach records use, the Mu-name -> Madden-enum
maps, the talent templates and the look lists. Nothing here reads a database, the network or the clock.

    build_profile(person, role, *, slant, scheme, playbook, tendencies, sliders,
                  background, contract, seed) -> dict

The seed is the full draw seed, `madden-coach:<coach_key>`; every draw comes from `random.Random(seed)`, so the
same inputs and seed give the same profile. The returned dict carries the `coach_madden_profiles` columns the
builder owns (`first_name` ... `seed`, plus `source = 'CREATED'` and `presentation_id = None`). The identity
columns `person_kind`/`person_id` are included when `person` supplies an `id`.

Inputs, and the shapes the league-office/carousel caller may pass:
  person       a Mu name string, or a dict with `name` (or `coach_name`/`coordinator_name`) and optional
               `first_name`/`last_name`, `id`, `traits`, `switches`, `team_philosophy`,
               `visuals_templates`.
  role         `HC`/`OC`/`DC` or the Madden `StaffPosition` (`HeadCoach`/`OffensiveCoordinator`/
               `DefensiveCoordinator`).
  slant        `OFFENSIVE`/`DEFENSIVE`/`CEO` (None defaults by role: OC -> OFFENSIVE, DC -> DEFENSIVE,
               HC -> OFFENSIVE, the `coaches.slant` column's default).
  scheme       one Mu scheme name (for the role's side), or a mapping with `offensive`/`defensive`
               (or `offensive_scheme`/`defensive_scheme`).
  playbook     the same shapes with Mu playbook names (`<City> Offense` / `<City> Defense`).
  tendencies   Mu's tendency dict (`pass_lean`, `fourth_down_aggressiveness`, `blitz_rate`,
               `man_coverage`; Low/Medium/High). Missing values are neutral.
  sliders      the person's 0-10 personality sliders (`confidence`/`ego`, `temperament`, `sociability`,
               `warmth`, `drive`). Missing values use the neutral 5 (`newsroom.personas.SLIDERS`).
  background   the text `staff_names.background` returns, or a mapping with `age` and `years`
               (`years_coaching` is accepted too).
  contract     the person's active `staff_contracts` row. The contract stays the truth there; the writer
               copies it into the Madden record, so the profile builder does not duplicate it.
  seed         `madden-coach:<coach_key>`.

An unmapped Mu playbook or scheme raises `ValueError` naming the value: guessing a Madden enum would put the
wrong system in the save, so the caller must add a mapping after a human decision.
"""
import random

# ------------------------------------------------------------------------------------------------- Spike A ---

# StaffArchetypeEnum real members (§3.1). Value 0 reads back as "First" but means OffensiveGuru.
ARCHETYPES = ("OffensiveGuru", "DefensiveGenius", "PersonnelCzar", "DevelopmentWizard", "HeadScout",
              "HeadTrainer", "MasterMotivator", "MasterMotivator_JohnMadden", "SharedCoach")

# StaffPosition real members (§3.2). The plan only creates the first three; the rest complete the enum.
POSITIONS = ("HeadCoach", "OffensiveCoordinator", "DefensiveCoordinator", "SpecialTeams", "Owner", "Scout",
             "Trainer", "GeneralManager", "PlayerPersonnel")

# CoachSpecialty real members (§3.3). SpecialTeams is unused in the save and never drawn for a coach.
SPECIALTIES = ("Quarterbacks", "RunningBacks", "Receivers", "OffensiveLine", "DefensiveLine", "Linebackers",
               "Secondary", "SpecialTeams")

# CharacterBodyType real members (§3.5); real coaches use only the first four.
BODY_TYPES = ("Standard", "Thin", "Muscular", "Heavy", "Freshman", "ReservedMain_1", "ReservedMain_2",
              "ReservedMain_3", "Standard_Alternate", "Thin_Alternate")
COACH_BODY_TYPES = ("Standard", "Thin", "Muscular", "Heavy")
# Usage counts across the 127 live coaches: the draw follows what real records look like.
BODY_TYPE_WEIGHTS = (102, 9, 11, 5)

# Personality / CoachDemeanor real members (§3.6).
PERSONALITIES = ("Leader", "Intense", "Entertainer", "TeamPlayer", "Unpredictable")
DEMEANORS = ("Classic", "Calm", "Intense")

# CoachFace values an invented coach may use (§3.12). Real coaches use their named preset; the 118 generic
# records use `Invalid_` or `MustBeUnique`.
FACE_SHAPE_FALLBACKS = ("Invalid_", "MustBeUnique")

# The 53 head assets invented/free-agent coaches use (§3.12).
GENERIC_HEAD_ASSETS = (
    "coachhead_M_0001_HS", "coachhead_M_0002_HS", "coachhead_M_0003_HS", "coachhead_M_0008_HS",
    "coachhead_M_0009_HS", "coachhead_M_0011_HS", "coachhead_M_0013_HS", "coachhead_M_0014_HS",
    "coachhead_M_0015_HS", "coachhead_M_0016_HS", "coachhead_M_0017_HS", "coachhead_M_0018_HS",
    "coachhead_M_0019_HS", "coachhead_M_0020_HS", "coachhead_M_0046_HS", "coachhead_M_0055_HS",
    "coachhead_M_0056_HS", "coachhead_M_0057_HS", "coachhead_M_0058_HS", "coachhead_M_0059_HS",
    "coachhead_M_0060_HS", "coachhead_M_0061_HS", "coachhead_M_0062_HS", "coachhead_M_0063_HS",
    "coachhead_M_0064_HS", "coachhead_M_0067_HS", "coachhead_M_0068_HS", "coachhead_M_0069_HS",
    "coachhead_M_0070_HS", "coachhead_M_0072_HS", "coachhead_M_0074_HS", "coachhead_M_0075_HS",
    "coachhead_M_0077_HS", "coachhead_M_0080_HS", "coachhead_M_0083_HS", "coachhead_M_0084_HS",
    "coachhead_M_0085_HS", "coachhead_M_0086_HS", "coachhead_M_0087_HS", "coachhead_M_0089_HS",
    "coachhead_M_0091_HS", "coachhead_M_0092_HS", "coachhead_M_0093_HS", "coachhead_M_0094_HS",
    "coachhead_M_0095_HS", "coachhead_M_0096_HS", "coachhead_M_0122_HS", "coachhead_M_0125_HS",
    "coachhead_M_0131_HS", "coachhead_M_0146_HS", "coachhead_M_0161_HS", "coachhead_M_0182_HS",
    "coachhead_M_0188_HS",
)

# Mu's playbook vocabulary is `<City> Offense` / `<City> Defense` for these 32 cities (§7.1), and the coaching
# carousel's own staff rows use the `<Nickname> Offense` / `<Nickname> Defense` form, so both are accepted below.
CITY_TO_TEAM = {
    "Arizona": "Cardinals", "Atlanta": "Falcons", "Baltimore": "Ravens", "Buffalo": "Bills",
    "Carolina": "Panthers", "Chicago": "Bears", "Cincinnati": "Bengals", "Cleveland": "Browns",
    "Dallas": "Cowboys", "Denver": "Broncos", "Detroit": "Lions", "Green Bay": "Packers",
    "Houston": "Texans", "Indianapolis": "Colts", "Jacksonville": "Jaguars", "Kansas City": "Chiefs",
    "LA Chargers": "Chargers", "LA Rams": "Rams", "Las Vegas": "Raiders", "Miami": "Dolphins",
    "Minnesota": "Vikings", "NY Giants": "Giants", "NY Jets": "Jets", "New England": "Patriots",
    "New Orleans": "Saints", "Philadelphia": "Eagles", "Pittsburgh": "Steelers", "San Francisco": "FortyNiners",
    "Seattle": "Seahawks", "Tampa Bay": "Buccaneers", "Tennessee": "Titans", "Washington": "Commanders",
}
PLAYBOOK_MAP = {f"{place} {side}": team
                for place, team in (*CITY_TO_TEAM.items(), *((t, t) for t in CITY_TO_TEAM.values()))
                for side in ("Offense", "Defense")}

# Mu scheme name -> Madden BaseScheme member (§7.2 `SCHEME_MAP`). Madden has no Erhardt-Perkins member, so it is
# mapped to MultiplePowerRun: that is what the save itself gives two of its four real Erhardt-Perkins coaches
# (Vrabel and McDaniels; Daboll is WestCoastZoneRun and Saleh WestCoastPowerRun). JR's decision, 2026-11-10.
# An unmapped name still raises rather than guessing.
SCHEME_MAP = {
    "Wide Zone West Coast": "WestCoastZoneRun",
    "West Coast": "WestCoastPowerRun",            # JR confirmed 2026-11-10
    "Spread": "Spread",
    "3-4": "Base3_4",
    "4-3": "Base4_3",
    "Erhardt-Perkins": "MultiplePowerRun",
}

# Talent templates by archetype (§5.3). Spike A has no MasterMotivator list (no live coach carries that
# archetype); the CEO archetype borrows DevelopmentWizard's list rather than inventing talent names.
TALENT_TEMPLATES = {
    "OffensiveGuru": {
        "gameday": ["Field General", "Dr. Dots", "Practician (QB)", "Cool Under Pressure", "Play Fake Magician",
                    "Outside the Club", "Sure Hands", "Angry Runner", "Insider Trading", "Move the Chains",
                    "Riot Shield", "Dashing Dots", "Trimmed Edges", "Happier Feet", "Interior Dominating",
                    "Blitz Buster", "Stamina Stealer", "Trench Barons", "Ahead of the Chains", "People Movers"],
        "playsheet": ["West Coast Playsheet", "Air Raid Gun Playsheet", "Screen Game Playsheet",
                      "Shot Plays Playsheet", "Heavy Sets Playsheet", "Red Zone Passing Playsheet",
                      "Shotgun 2-Back Playsheet", "Air Raid Pistol Playsheet", "Wide Slot Playsheet",
                      "Wide Trips Playsheet", "Run N Shoot Gun Playsheet", "Wide Empty Playsheet"],
    },
    "DefensiveGenius": {
        "gameday": ["Sideline 2 Sideline", "Behind the Chains", "Inside Stuffed", "Practician (LB)",
                    "Interior Havoc", "Sheddables", "Aerial Suppression", "Fool Me Never", "Mano a Mano",
                    "No Fly Zone", "Practician (CB)", "Trench Goblins", "Mr. Dream", "Island Kings",
                    "Hitamus Stickumus", "Cover 2 Guru", "Ground Guardians", "Bookend Busters",
                    "Cover 4 Guru", "Linebacker Lens"],
        "playsheet": ["4-3 Playsheet", "4-2-5 Playsheet", "Batch O' Blitzes Playsheet", "Run Stop Playsheet",
                      "Nickel Mug Playsheet", "3-4 Playsheet", "Dime Playsheet", "Nickel Playsheet",
                      "Dollar Playsheet", "Master of Disguise Playsheet", "3-3-5 Playsheet",
                      "QB Contain Playsheet"],
    },
    "DevelopmentWizard": {
        "gameday": ["Practician (OL)", "Stamina Stealer", "Ahead of the Chains", "After School Tutor",
                    "Hitamus Stickumus", "Insider Trading", "Outside the Club", "Big Back", "Fit Finder",
                    "People Movers", "Cool Under Pressure", "Dr. Dots", "Trench Barons", "Shield Finder",
                    "Dashing Dots", "Talent Evaluator", "Angry Runner", "Stim Shot", "Riot Shield", "Sure Hands"],
        "playsheet": ["Heavy Sets Playsheet", "Gadget Playsheet", "Heavy I Playsheet", "Heavy Strong Playsheet",
                      "West Coast Playsheet", "Run N Shoot Playsheet", "Wide Empty Playsheet",
                      "Red Zone Passing Playsheet", "Screen Game Playsheet", "Triple Option Playsheet",
                      "Pitch Perfect Playsheet", "QB Contain Playsheet"],
    },
}
MASTER_MOTIVATOR_TEMPLATE = "DevelopmentWizard"

# Level from years coaching (plan D9: modest starting level; §5.2's medians rise with level). A coach gets
# the level of the highest threshold at or below his years. Spans the observed 1-41.
LEVEL_BY_YEARS = (
    (0, 1), (2, 2), (5, 3), (8, 4), (11, 5), (14, 6), (18, 8), (22, 10),
    (26, 12), (30, 15), (35, 19), (40, 24), (45, 30), (50, 36), (55, 41),
)
# §5.2's fallback talent counts (no real coach list is available to clone in a pure builder).
def talent_counts(level):
    gameday = min(max(7 + round(0.45 * level), 5), 24)
    playsheet = min(max(1 + round(0.16 * level), 1), 10)
    return gameday, playsheet

# Specialty: the position group the coach teaches (§3.3). Draw weighted by what the 127 live coaches use.
SPECIALTY_WEIGHTS = {
    "OffensiveGuru": (("Quarterbacks", 41), ("Receivers", 11), ("OffensiveLine", 9), ("RunningBacks", 5)),
    "DefensiveGenius": (("Secondary", 23), ("Linebackers", 22), ("DefensiveLine", 16)),
}
SPECIALTY_WEIGHTS_ALL = (("Quarterbacks", 41), ("RunningBacks", 5), ("Receivers", 11), ("OffensiveLine", 9),
                         ("DefensiveLine", 16), ("Linebackers", 22), ("Secondary", 23))
SPECIALTY_RATING_KEYS = {
    "Quarterbacks": ("COACH_QB",),
    "RunningBacks": ("COACH_RB",),
    "Receivers": ("COACH_WR",),
    "OffensiveLine": ("COACH_OL",),
    "DefensiveLine": ("COACH_DL",),
    "Linebackers": ("COACH_LB",),
    "Secondary": ("COACH_DB", "COACH_S"),
}

# The ten position ratings and the two side ratings the record carries (§4/Appendix A). `COACH_RATING` is
# the overall; Madden recomputes it on a release, but a created record needs one.
POSITION_RATING_KEYS = ("COACH_QB", "COACH_RB", "COACH_WR", "COACH_OL", "COACH_DL", "COACH_LB",
                        "COACH_DB", "COACH_S", "COACH_K", "COACH_P")
OFFENSIVE_RATING_KEYS = ("COACH_QB", "COACH_RB", "COACH_WR", "COACH_OL")
DEFENSIVE_RATING_KEYS = ("COACH_DL", "COACH_LB", "COACH_DB", "COACH_S")

# Mu tendency (Low/Medium/High) -> the COACH_*TENDENCY* int Madden stores. §3.6/Appendix A show ints around
# 35/50/65. `man_coverage` has no Madden tendency field, so it is not written into this JSON.
TENDENCY_LEVELS = {"low": 35, "medium": 50, "high": 65}
TENDENCY_FIELDS = ("COACH_OFFTENDENCYRUNPASS", "COACH_OFFTENDENCYAGGRESSCONSERV",
                   "COACH_DEFTENDENCYAGGRESSCONSERV", "COACH_DEFTENDENCYRUNPASS", "COACH_RBTENDENCY")

# Visuals templates: one save coach per position and body type from Spike A's Appendix A (CAREER-MUBASE
# PresentationIds). The writer clones that record's CharacterVisuals and wardrobe (§2.5). Where the save has
# no same-position coach of a body type (an OC Thin, a DC Heavy), the same position's Standard record is used.
VISUALS_TEMPLATES = {
    ("HeadCoach", "Standard"): 749,              # Ben Johnson
    ("HeadCoach", "Thin"): 378,                  # Sean McVay
    ("HeadCoach", "Muscular"): 374,              # Dan Campbell
    ("HeadCoach", "Heavy"): 86,                  # Todd Bowles
    ("OffensiveCoordinator", "Standard"): 150,   # Klayton Adams
    ("OffensiveCoordinator", "Thin"): 150,       # no OC Thin in CAREER-MUBASE; Standard fallback
    ("OffensiveCoordinator", "Muscular"): 141,   # Jim Bob Cooter
    ("OffensiveCoordinator", "Heavy"): 222,      # Brian Daboll
    ("DefensiveCoordinator", "Standard"): 14,    # Ian Alexander
    ("DefensiveCoordinator", "Thin"): 129,       # Steve Spagnuolo
    ("DefensiveCoordinator", "Muscular"): 152,   # Kelvin Sheppard
    ("DefensiveCoordinator", "Heavy"): 14,       # no DC Heavy in CAREER-MUBASE; Standard fallback
}

# Every live coach record stores the placeholder Weight = 10, which is outside the field's own schema range (int,
# 150-512), so the save writer's validation refuses it. A created coach gets a plausible human weight drawn from his
# body type instead. Note the divergence from the 127 real records; Spike C checks that Madden accepts it.
COACH_WEIGHT_BY_BODY = {"Thin": (160, 185), "Standard": (180, 215), "Muscular": (200, 235), "Heavy": (225, 290)}
COACH_WEIGHT_DEFAULT = (180, 215)
COACH_HEIGHT_RANGE = (68, 78)      # inches; the 127 live records run 68-77
COACH_AGE_RANGE = (32, 72)         # plan D9's range for a created coach

# Slider -> Personality / Demeanor (§2.4 "through DEMEANOR_RULES"). A rule fires when all its demands hold,
# in order; the empty demand list is the neutral floor. Missing sliders read the neutral 5.
DEMEANOR_RULES = (
    {"demands": (("temperament", ">=", 7),), "value": "Intense"},
    {"demands": (("temperament", "<=", 3),), "value": "Calm"},
    {"demands": (), "value": "Classic"},
)
PERSONALITY_RULES = (
    {"demands": (("temperament", ">=", 8),), "value": "Intense"},
    {"demands": (("confidence", ">=", 7), ("drive", ">=", 6)), "value": "Leader"},
    {"demands": (("sociability", ">=", 7),), "value": "Entertainer"},
    {"demands": (("temperament", ">=", 6), ("warmth", "<=", 4)), "value": "Unpredictable"},
    {"demands": (), "value": "TeamPlayer"},
)

_ROLE_POSITION = {
    "HC": "HeadCoach", "OC": "OffensiveCoordinator", "DC": "DefensiveCoordinator",
    "HeadCoach": "HeadCoach", "OffensiveCoordinator": "OffensiveCoordinator",
    "DefensiveCoordinator": "DefensiveCoordinator",
}
_ROLE_SIDE = {"HC": None, "HeadCoach": None,
              "OC": "OFFENSIVE", "OffensiveCoordinator": "OFFENSIVE",
              "DC": "DEFENSIVE", "DefensiveCoordinator": "DEFENSIVE"}
_SLANT_SIDE = {"OFFENSIVE": "offense", "DEFENSIVE": "defense", "CEO": "offense"}


def _clamp(value, low, high):
    return max(low, min(high, value))


def _role_position(role):
    try:
        return _ROLE_POSITION[str(role).strip()]
    except (KeyError, AttributeError):
        raise ValueError(f"unknown role {role!r} (HC, OC, DC or a StaffPosition)") from None


def _norm_slant(slant, role):
    if slant is None:
        return _ROLE_SIDE.get(str(role).strip()) or "OFFENSIVE"
    value = str(slant).strip().upper()
    if value not in _SLANT_SIDE:
        raise ValueError(f"unknown slant {slant!r} (OFFENSIVE, DEFENSIVE or CEO)")
    return value


def _person_dict(person):
    return person if isinstance(person, dict) else {}


def _name_parts(person):
    """`(first_name, last_name)` split from Mu's full name (or taken from a dict's explicit fields)."""
    if isinstance(person, dict):
        first, last = person.get("first_name"), person.get("last_name")
        if first and last:
            return str(first).strip(), str(last).strip()
        name = person.get("name") or person.get("coach_name") or person.get("coordinator_name") or ""
    else:
        name = person or ""
    parts = str(name).strip().split(None, 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    if len(parts) == 1:
        return parts[0], ""
    raise ValueError("person needs a full name (first and last) for the Madden record")


def _background_age_years(background, rng):
    """Age and years coaching from `staff_names.background` (§2.4), clamped to 32-72.

    The current `staff_names.background` returns only the narrative text, so a mapping may carry the age and
    years; without them both are drawn from the same seed stream. Years never exceed age - 22 and 45.
    """
    age = years = None
    if isinstance(background, dict):
        age = background.get("age")
        years = background.get("years", background.get("years_coaching"))
    if age is None:
        age = rng.randint(36, 66)
    age = _clamp(int(age), *COACH_AGE_RANGE)
    if years is None:
        years = rng.randint(2, min(40, age - 24))
    years = _clamp(int(years), 0, min(45, age - 22))
    return age, years


def _own_side(role_position, role_side, slant):
    """The side the person actually coaches; a head coach's own side is his slant, a coordinator's is his job."""
    if role_position == "HeadCoach":
        return "defense" if slant == "DEFENSIVE" else "offense"
    return "defense" if role_side == "DEFENSIVE" else "offense"


_SIDE_KEYS = {
    "offense": ("offense", "offensive", "offensive_scheme", "offensive_playbook"),
    "defense": ("defense", "defensive", "defensive_scheme", "defensive_playbook"),
}


def _mapping_side(value, side, own):
    """One Mu name (the person's own side only), or the value for `side` from a mapping."""
    if isinstance(value, dict):
        for key in _SIDE_KEYS[side]:
            if value.get(key) is not None:
                return value[key]
        return None
    return value if side == own else None


def _map_playbook(value):
    if value is None:
        return None
    try:
        return PLAYBOOK_MAP[str(value).strip()]
    except KeyError:
        raise ValueError(f"no Madden playbook for Mu playbook {value!r}; add a PLAYBOOK_MAP entry after review"
                         ) from None


def _map_scheme(value):
    if value is None:
        return None
    try:
        return SCHEME_MAP[str(value).strip()]
    except KeyError:
        raise ValueError(f"no Madden scheme for Mu scheme {value!r}; add a SCHEME_MAP entry after review"
                         ) from None


def _side_names(role_position, role_side, slant, scheme, playbook):
    """{'offense': ..., 'defense': ...} Mu names for both sides, and the person's own side.

    A mapping may set each side; a single name is the person's own side, because that is the only system the
    Mu row describes (a coordinator has one scheme/playbook; the head coach's other side stays with the other
    coordinator and is left NULL rather than guessed)."""
    own = _own_side(role_position, role_side, slant)
    other = "defense" if own == "offense" else "offense"
    schemes = {own: _mapping_side(scheme, own, own), other: _mapping_side(scheme, other, own)}
    playbooks = {own: _mapping_side(playbook, own, own), other: _mapping_side(playbook, other, own)}
    return schemes, playbooks, own


def _teacher_on(person):
    """True when the person carries the progression trait TEACHER and its switch is not off.

    The personality code spells the trait `TEACHER` (`engines.personality_development.TEACHER_TRAIT`) and reads
    traits from `persona['traits']`. A `{'switches': {'TEACHER': False}}` entry (or a `teacher_switch: False`)
    turns the archetype override off; with no switch the set trait applies.
    """
    if isinstance(person, dict):
        traits = {str(t).strip().upper() for t in (person.get("traits") or [])}
        switches = person.get("switches") or {}
        if switches.get("TEACHER") is False or person.get("teacher_switch") is False:
            return False
        return "TEACHER" in traits
    return False


def _archetype(role_position, slant, person):
    if _teacher_on(person):
        return "DevelopmentWizard"
    if role_position == "OffensiveCoordinator":
        return "OffensiveGuru"
    if role_position == "DefensiveCoordinator":
        return "DefensiveGenius"
    return {"OFFENSIVE": "OffensiveGuru", "DEFENSIVE": "DefensiveGenius", "CEO": "MasterMotivator"}[slant]


def _template(archetype):
    return TALENT_TEMPLATES.get(archetype) or TALENT_TEMPLATES[MASTER_MOTIVATOR_TEMPLATE]


def _level(years):
    level = 1
    for threshold, value in LEVEL_BY_YEARS:
        if years >= threshold:
            level = value
    return _clamp(level, 1, 41)


def _talents(archetype, level):
    """The first N template names at this level, in the §2.1 layout. CurrentTier rises with the level."""
    template = _template(archetype)
    gameday_n, playsheet_n = talent_counts(level)
    tier = _clamp(level // 12, 0, 3)
    out = [{"slot": "Gameday", "talent": name, "tier": tier} for name in template["gameday"][:gameday_n]]
    out += [{"slot": "Playsheet", "talent": name, "tier": tier} for name in template["playsheet"][:playsheet_n]]
    return out


def _specialty(archetype, rng):
    pool = SPECIALTY_WEIGHTS.get(archetype, SPECIALTY_WEIGHTS_ALL)
    names = [name for name, _ in pool]
    weights = [weight for _, weight in pool]
    return rng.choices(names, weights=weights, k=1)[0]


def _ratings(specialty, side, rng):
    """Position ratings centred on the specialty, plus the two side ratings and the overall (§2.4)."""
    base = rng.randint(45, 72)
    ratings = {key: _clamp(base + rng.randint(-12, 12), 25, 90) for key in POSITION_RATING_KEYS}
    for key in SPECIALTY_RATING_KEYS.get(specialty, ()):
        ratings[key] = _clamp(base + 18 + rng.randint(0, 5), 45, 99)
    off = round(sum(ratings[k] for k in OFFENSIVE_RATING_KEYS) / len(OFFENSIVE_RATING_KEYS))
    de = round(sum(ratings[k] for k in DEFENSIVE_RATING_KEYS) / len(DEFENSIVE_RATING_KEYS))
    bump = 8 if side == "offense" else -8
    ratings["COACH_OFFENSE"] = _clamp(off + bump, 25, 99)
    ratings["COACH_DEFENSE"] = _clamp(de - bump, 25, 99)
    ratings["COACH_RATING"] = _clamp(round(sum(ratings[k] for k in POSITION_RATING_KEYS)
                                          / len(POSITION_RATING_KEYS)), 30, 99)
    return ratings


def _tendency_value(value):
    if value is None:
        return 50
    if isinstance(value, bool):
        return 50
    if isinstance(value, (int, float)):
        return _clamp(int(value), 0, 100)
    return TENDENCY_LEVELS.get(str(value).strip().lower(), 50)


def _tendencies(tendencies):
    """Mu's Low/Medium/High tendencies -> the COACH_*TENDENCY* int fields (neutral 50 when absent)."""
    tendencies = tendencies if isinstance(tendencies, dict) else {}
    return {
        "COACH_OFFTENDENCYRUNPASS": _tendency_value(tendencies.get("pass_lean")),
        "COACH_OFFTENDENCYAGGRESSCONSERV": _tendency_value(tendencies.get("fourth_down_aggressiveness")),
        "COACH_DEFTENDENCYAGGRESSCONSERV": _tendency_value(tendencies.get("blitz_rate")),
        "COACH_DEFTENDENCYRUNPASS": 50,
        "COACH_RBTENDENCY": 50,
    }


def _slider(sliders, *names):
    for name in names:
        value = (sliders or {}).get(name)
        if value is not None:
            return _clamp(int(value), 0, 10)
    return 5


def _rule_value(rules, sliders):
    for rule in rules:
        if all(_compare(_slider(sliders, name), op, target) for name, op, target in rule["demands"]):
            return rule["value"]


def _compare(value, op, target):
    return {">=": value >= target, "<=": value <= target, ">": value > target, "<": value < target}[op]


def _personality(sliders):
    return _rule_value(PERSONALITY_RULES, sliders)


def _demeanor(sliders):
    return _rule_value(DEMEANOR_RULES, sliders)


def _look(rng):
    body_type = rng.choices(COACH_BODY_TYPES, weights=BODY_TYPE_WEIGHTS, k=1)[0]
    height = _clamp(rng.randint(68, 77), *COACH_HEIGHT_RANGE)
    face_shape = rng.choice(FACE_SHAPE_FALLBACKS)
    generic_head = rng.choice(GENERIC_HEAD_ASSETS)
    return body_type, height, face_shape, generic_head


def _visuals_template(position, body_type, person):
    custom = (person if isinstance(person, dict) else {}).get("visuals_templates")
    if isinstance(custom, dict):
        for key in ((position, body_type), body_type):
            if key in custom:
                return int(custom[key])
    return VISUALS_TEMPLATES[(position, body_type)]


def build_profile(person, role, *, slant, scheme, playbook, tendencies, sliders,
                  background, contract, seed):
    """Build the `coach_madden_profiles` row for one invented coach or coordinator (see the module docs).

    `contract` is accepted because the caller holds the person's `staff_contracts` row; that table stays the
    truth for salary and years, so nothing contract-shaped goes into this row.
    """
    if not seed:
        raise ValueError("build_profile needs the draw seed (madden-coach:<coach_key>)")
    rng = random.Random(seed)
    person_info = _person_dict(person)

    position = _role_position(role)
    role_side = _ROLE_SIDE[str(role).strip()]
    slant = _norm_slant(slant, role)
    first_name, last_name = _name_parts(person)
    age, years = _background_age_years(background, rng)
    archetype = _archetype(position, slant, person)
    level = _level(years)

    schemes, playbooks, side = _side_names(position, role_side, slant, scheme, playbook)

    body_type, height, face_shape, generic_head = _look(rng)
    specialty = _specialty(archetype, rng)
    ratings = _ratings(specialty, side, rng)

    row = {
        "presentation_id": None,
        "first_name": first_name,
        "last_name": last_name,
        "age": age,
        "position": position,
        "archetype": archetype,
        "specialty": specialty,
        "level": level,
        "years_coaching": years,
        "offensive_playbook": _map_playbook(playbooks["offense"]),
        "defensive_playbook": _map_playbook(playbooks["defense"]),
        "offensive_scheme": _map_scheme(schemes["offense"]),
        "defensive_scheme": _map_scheme(schemes["defense"]),
        "team_philosophy": person_info.get("team_philosophy"),
        "personality": _personality(sliders),
        "demeanor": _demeanor(sliders),
        "body_type": body_type,
        "height": height,
        "weight": random.Random(f"{seed}:weight").randint(*COACH_WEIGHT_BY_BODY.get(body_type, COACH_WEIGHT_DEFAULT)),
        "face_shape": face_shape,
        "generic_head": generic_head,
        "talents": _talents(archetype, level),
        "ratings": ratings,
        "tendencies": _tendencies(tendencies),
        "visuals_template": _visuals_template(position, body_type, person),
        "portrait_id": 0,
        "portrait_status": "NONE",
        "headshot_path": None,
        "seed": seed,
        "source": "CREATED",
        "written_at": None,
    }
    if person_info.get("id") is not None:
        row["person_kind"] = "COACH" if position == "HeadCoach" else "COORD"
        row["person_id"] = int(person_info["id"])
    return row
