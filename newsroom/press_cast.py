"""Press conference casting: pre-game interest and the national press pool
(docs/done/PRESS_CONFERENCE_PLAN.md §2.4; PROJECT_SPEC §2.22).

Casting answers two questions, using only what was known before kickoff:
who is interested in a game, and which reporters attend which coach's postgame presser.
It never reads a final score, a post-game Excitement Index, or the current season's
``team_seasons.popularity_index`` (plan R3: the result must not leak into casting).

Public API::

    pregame_interest(cur, game) -> {"interest": float, "inputs": {...}}
    cast_week(cur, season, week) -> {game_id: Cast}

    Cast = {"game_id", "interest", "tier", "inputs",
            "nationals": [Attendee], "beats": {team_id: [Attendee]}}
    Attendee = {"agent_id", "person_key", "archetype", "display_name", "handle",
                "role": "BEAT" | "NATIONAL", "pool_tier"}

``cur`` is any DB-API cursor. Every function takes a cursor and never commits.
"""

import json
import os
import random

from engines.gotw_engine import display_excitement
from engines.market_constants import market
from pipeline.mod_packs import PRIMETIME, broadcast_window

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config", "press_pool.json")
PERSONAS_PATH = os.path.join(os.path.dirname(__file__), "config", "agent_personas.json")

# Pre-game interest (0–100): the approved §2.4 formula. The weights are first guesses.
EXCITEMENT_WEIGHT = 0.45
MARKET_WEIGHT = 0.35
FORM_WEIGHT = 0.20
PRIMETIME_BONUS = 8
DIVISION_BONUS = 5
RACE_BONUS = 5
RACE_WEEK = 15
FORM_FULL_GAMES = 4

# Playoff rounds. The round's floor is the minimum interest a playoff game carries.
PLAYOFF_FLOORS = {"WILD_CARD": 70, "DIVISIONAL": 80, "CONFERENCE": 90, "SUPER_BOWL": 100}
DEFAULT_PLAYOFF_FLOOR = PLAYOFF_FLOORS["WILD_CARD"]

TIER_ORDER = ("QUIET", "NOTABLE", "BIG", "MARQUEE")
TIER_COUNTS = {"QUIET": (0, 1), "NOTABLE": (1, 2), "BIG": (2, 4), "MARQUEE": (4, 6)}
# The pool tiers a game at each interest tier may draw from (the §2.4 table is cumulative).
TIER_POOLS = {
    "QUIET": ("INSIDER",),
    "NOTABLE": ("INSIDER", "REPORTER"),
    "BIG": ("INSIDER", "REPORTER", "COLUMNIST"),
    "MARQUEE": ("INSIDER", "REPORTER", "COLUMNIST", "TV"),
}
QUIET_INSIDER_CHANCE = 1.0 / 3.0
SUPER_BOWL_MAX_NATIONALS = 8

# The national candidates named in the plan's S5 survey. Every one that is not excluded in
# press_pool.json must be placed in a tier; validate_pool() enforces it.
NATIONAL_CANDIDATES = (
    "ACHO", "BARNWELL", "BERRY", "CHADIHA", "CLARK", "COWHERD", "EISEN", "ESPN", "HERBSTREIT",
    "JJ_WATT", "JOYNER", "KIMES", "MCAFEE", "MEL_KIPER", "OCHOCINCO", "ORLOVSKY", "RAPOPORT",
    "RG3", "ROSENTHAL", "SANDO", "SCHEFTER", "SHANNON", "SIMMONS", "SKIP_BAYLESS", "STEPHEN_A",
    "THE_ATHLETIC", "WINGO", "YATES",
)

# Mirror of newsroom.social_desk.FORMER_TEAM_SLANT, used only if that module can't be imported.
_FORMER_TEAM_SLANT_FALLBACK = {
    "OCHOCINCO": "CIN", "SHANNON": "DEN", "JJ_WATT": "HOU", "RG3": "WAS", "CLARK": "PIT", "MCAFEE": "IND",
}

# Flip when personality.relationship_context is available to this consumer. It stays False:
# the relationships plan (PERSONALITY_RELATIONSHIPS_PLAN §0b, §2.1) owns trust and its
# relationship_context does not exist for casting yet, so no persona_relationships row is read.
RELATIONSHIP_CONTEXT_AVAILABLE = False


# --------------------------------------------------------------------------------------------- helpers

def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


def _former_team_slant() -> dict:
    """Archetype -> former team abbreviation (the ex-player pundits' old team)."""
    try:
        from newsroom.social_desk import FORMER_TEAM_SLANT
        return dict(FORMER_TEAM_SLANT)
    except Exception:
        return dict(_FORMER_TEAM_SLANT_FALLBACK)


def _is_playoff(game_type) -> bool:
    return (game_type or "REGULAR_SEASON") != "REGULAR_SEASON"


def _playoff_floor(game_type) -> int:
    return int(PLAYOFF_FLOORS.get(game_type, DEFAULT_PLAYOFF_FLOOR))


def _rescaled_excitement(projected_excitement):
    """display_excitement (70–100) rescaled to 0–100; 50 when the projection is missing."""
    shown = display_excitement(projected_excitement)
    if shown is None:
        return 50.0
    return round(_clamp((float(shown) - 70.0) / 30.0 * 100.0, 0.0, 100.0), 2)


def _teams_for_game(cur, home_id, away_id) -> dict:
    """{team_id: {id, name, abbreviation, conference, division}} for the game's two teams."""
    cur.execute(
        "SELECT id, name, abbreviation, conference, division FROM teams WHERE id IN (%s, %s)",
        (home_id, away_id),
    )
    out = {}
    for team_id, name, abbreviation, conference, division in cur.fetchall():
        out[team_id] = {
            "id": team_id, "name": name, "abbreviation": abbreviation,
            "conference": conference, "division": division,
        }
    for team_id in (home_id, away_id):
        out.setdefault(team_id, {"id": team_id, "name": None, "abbreviation": None,
                                 "conference": None, "division": None})
    return out


def _market_b(info) -> float:
    """Base Market Gravity B for a team (0–100); the default when the team is unknown."""
    return float(market((info or {}).get("name") or "")["B"])


def _win_share(cur, season, before_week=None) -> dict:
    """{team_id: (wins + ties/2, games)} over completed regular-season games, before `week` when given."""
    if before_week is None:
        cur.execute(
            "SELECT home_team_id, away_team_id, home_score, away_score FROM games "
            "WHERE season = %s AND game_type = 'REGULAR_SEASON' AND status = 'COMPLETED'",
            (season,),
        )
    else:
        cur.execute(
            "SELECT home_team_id, away_team_id, home_score, away_score FROM games "
            "WHERE season = %s AND game_type = 'REGULAR_SEASON' AND status = 'COMPLETED' AND week < %s",
            (season, before_week),
        )
    out = {}
    for home, away, home_score, away_score in cur.fetchall():
        if home is None or away is None or home_score is None or away_score is None:
            continue
        for team, mine, theirs in ((home, home_score, away_score), (away, away_score, home_score)):
            wins, games = out.get(team, (0.0, 0))
            if mine > theirs:
                wins += 1.0
            elif mine == theirs:
                wins += 0.5
            out[team] = (wins, games + 1)
    return out


def _form(team_id, now: dict, last: dict) -> float:
    """Win share in games before this week, blended with last season's for the first 4 games
    (the same rule as pipeline.mod_packs.form)."""
    wins, games = now.get(team_id, (0.0, 0))
    last_wins, last_games = last.get(team_id, (0.0, 0))
    last_share = last_wins / last_games if last_games else 0.5
    if games >= FORM_FULL_GAMES:
        return wins / games
    return (wins + last_share * (FORM_FULL_GAMES - games)) / FORM_FULL_GAMES


def _win_pct(share) -> float:
    wins, games = share
    return wins / games if games else 0.0


# ------------------------------------------------------------------------------------- pre-game interest

def pregame_interest(cur, game: dict) -> dict:
    """Pre-game interest (0–100) for one game, using only pre-kickoff inputs.

    ``game`` carries ``home_team_id``, ``away_team_id``, ``season``, ``week`` and optionally
    ``game_type`` (default REGULAR_SEASON), ``game_time`` and ``projected_excitement``.
    Callers must pass the raw projected E that was stored before kickoff; never pass
    ``excitement_index`` or any post-game value.
    """
    home_id = game["home_team_id"]
    away_id = game["away_team_id"]
    season = int(game["season"])
    week = int(game["week"])
    game_type = game.get("game_type") or "REGULAR_SEASON"
    projected = game.get("projected_excitement")
    projected = float(projected) if projected is not None else None

    teams = _teams_for_game(cur, home_id, away_id)
    home_team, away_team = teams[home_id], teams[away_id]
    now_share = _win_share(cur, season, before_week=week)
    last_share = _win_share(cur, season - 1)

    excitement = _rescaled_excitement(projected)
    market_home = _market_b(home_team)
    market_away = _market_b(away_team)
    market_mean = (market_home + market_away) / 2.0
    form_home = _form(home_id, now_share, last_share)
    form_away = _form(away_id, now_share, last_share)
    form_mean = (form_home + form_away) / 2.0

    window = broadcast_window(game.get("game_time") or "", away_team["conference"])
    primetime = PRIMETIME_BONUS if window in PRIMETIME else 0
    division = DIVISION_BONUS if (
        home_team["division"] and home_team["division"] == away_team["division"]
    ) else 0
    race = RACE_BONUS if (
        week >= RACE_WEEK
        and _win_pct(now_share.get(home_id, (0.0, 0))) >= 0.5
        and _win_pct(now_share.get(away_id, (0.0, 0))) >= 0.5
    ) else 0

    excitement_component = EXCITEMENT_WEIGHT * excitement
    market_component = MARKET_WEIGHT * market_mean
    form_component = FORM_WEIGHT * (100.0 * form_mean)
    base = excitement_component + market_component + form_component + primetime + division + race

    floor = None
    if _is_playoff(game_type):
        floor = _playoff_floor(game_type)
        base = max(base, float(floor))
    interest = round(_clamp(base, 0.0, 100.0), 2)

    inputs = {
        "season": season,
        "week": week,
        "game_type": game_type,
        "projected_excitement": projected,
        "display_excitement": display_excitement(projected),
        "excitement_rescaled": excitement,
        "market_home": round(market_home, 2),
        "market_away": round(market_away, 2),
        "market_mean": round(market_mean, 2),
        "form_home": round(form_home, 4),
        "form_away": round(form_away, 4),
        "form_mean": round(form_mean, 4),
        "home_abbreviation": home_team["abbreviation"],
        "away_abbreviation": away_team["abbreviation"],
        "broadcast_window": window,
        "primetime_bonus": primetime,
        "division_bonus": division,
        "race_bonus": race,
        "playoff_floor": floor,
        "weighted": {
            "excitement": round(excitement_component, 2),
            "market": round(market_component, 2),
            "form": round(form_component, 2),
        },
    }
    return {"interest": interest, "inputs": inputs}


def interest_tier(interest: float, game_type: str = "REGULAR_SEASON") -> str:
    """QUIET (<45), NOTABLE (45–65), BIG (65–80), MARQUEE (>=80 or any playoff game)."""
    if _is_playoff(game_type):
        return "MARQUEE"
    if interest >= 80:
        return "MARQUEE"
    if interest >= 65:
        return "BIG"
    if interest >= 45:
        return "NOTABLE"
    return "QUIET"


# ------------------------------------------------------------------------------------------ the pool

def load_pool(path: str = None) -> dict:
    with open(path or CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def _persona_archetypes(path: str = None) -> set:
    with open(path or PERSONAS_PATH, encoding="utf-8") as f:
        return set(json.load(f).get("archetypes", {}))


def validate_pool(pool: dict, archetypes=None) -> dict:
    """Validate press_pool.json: known archetypes, no overlaps, every S5 national candidate placed,
    neutral relationship weighting, and a one-line note for every archetype. Returns the pool."""
    if archetypes is None:
        archetypes = _persona_archetypes()
    known = {str(a).upper() for a in archetypes}

    tiers = pool.get("tiers") or {}
    if set(tiers) != {"INSIDER", "REPORTER", "COLUMNIST", "TV"}:
        raise ValueError(f"tiers must be INSIDER, REPORTER, COLUMNIST and TV; got {sorted(tiers)}")

    placed, seen = {}, set()
    for tier, names in tiers.items():
        if not names:
            raise ValueError(f"tier {tier} is empty")
        for name in names:
            arch = str(name).upper()
            if arch not in known:
                raise ValueError(f"unknown archetype in tier {tier}: {name}")
            if arch in seen:
                raise ValueError(f"archetype {arch} appears more than once in the tiers")
            seen.add(arch)
            placed[arch] = tier

    excluded = [str(a).upper() for a in (pool.get("excluded") or [])]
    for arch in excluded:
        if arch not in known:
            raise ValueError(f"unknown excluded archetype: {arch}")
        if arch in placed:
            raise ValueError(f"archetype {arch} is both placed in a tier and excluded")

    missing = [a for a in NATIONAL_CANDIDATES if a not in excluded and a not in placed]
    if missing:
        raise ValueError(f"national candidate(s) not placed in a tier: {', '.join(sorted(missing))}")

    weights = pool.get("weights") or {}
    if float(weights.get("former_team_bonus", 0)) <= 0:
        raise ValueError("weights.former_team_bonus must be positive")
    if float(weights.get("relationship_bonus", 0)) != 0.0:
        raise ValueError("weights.relationship_bonus must stay 0 until relationship_context exists")

    notes = pool.get("notes") or {}
    missing_notes = sorted(known - {str(k).upper() for k in notes})
    if missing_notes:
        raise ValueError(f"missing a one-line note for: {', '.join(missing_notes)}")
    for key, note in notes.items():
        if not isinstance(note, str) or not note.strip():
            raise ValueError(f"empty note for {key}")
    return pool


def national_weight(archetype: str, home_abbr, away_abbr, weights: dict = None,
                    relationship_multiplier: float = 1.0, slant: dict = None) -> float:
    """Weight for a national in this game: former-team slants are weighted up, the rest neutral."""
    weights = weights or load_pool()["weights"]
    slant = slant if slant is not None else _former_team_slant()
    weight = 1.0
    former = slant.get(str(archetype).upper())
    if former and former in {home_abbr, away_abbr}:
        weight *= float(weights.get("former_team_bonus", 1.0))
    # relationship_context would multiply by (1 + relationship_bonus) here; neutral until it exists.
    return weight * float(relationship_multiplier)


def _relationship_multiplier(cur, person_key, home_team_id, away_team_id, weights: dict) -> float:
    """CLEARLY MARKED SLOT: relationship_context does not exist for this consumer yet.

    The relationships plan (PERSONALITY_RELATIONSHIPS_PLAN §0b, §2.1) owns one-way trust; when
    ``personality.relationships.relationship_context(state, target=...)`` is wired in for casting,
    a named tie to either team would return ``1 + weights['relationship_bonus']``. Today this is
    neutral: it reads nothing and, in particular, never reads ``persona_relationships``.
    """
    if not RELATIONSHIP_CONTEXT_AVAILABLE:
        return 1.0
    return 1.0 + float(weights.get("relationship_bonus", 0.0))


# ------------------------------------------------------------------------------------------ attendees

def _national_records(cur, pool: dict) -> dict:
    """{archetype: attendee seed} for every national in the pool, first agent_memories row per archetype."""
    archetypes = sorted({a for names in pool["tiers"].values() for a in names})
    cur.execute(
        "SELECT agent_id, archetype, person_key, handle, display_name FROM agent_memories "
        "WHERE UPPER(archetype) = ANY(%s) ORDER BY id",
        (archetypes,),
    )
    found = {}
    for agent_id, archetype, person_key, handle, display_name in cur.fetchall():
        key = str(archetype or "").upper()
        if key in found:
            continue
        found[key] = {
            "agent_id": agent_id,
            "person_key": person_key or agent_id,
            "display_name": display_name or agent_id,
            "handle": handle or ("@" + str(display_name or agent_id).replace(" ", "")),
        }
    out = {}
    for arch in archetypes:
        out[arch] = found.get(arch) or {
            "agent_id": arch,
            "person_key": arch,
            "display_name": arch.replace("_", " ").title(),
            "handle": "@" + arch.replace("_", ""),
        }
    return out


def _beat_reporters(cur) -> dict:
    """{team_id: [Attendee]} for every LOCAL_BEAT_REPORTER with an assigned team."""
    cur.execute(
        "SELECT agent_id, archetype, person_key, handle, display_name, assigned_team_id "
        "FROM agent_memories WHERE archetype = 'LOCAL_BEAT_REPORTER' AND assigned_team_id IS NOT NULL "
        "ORDER BY assigned_team_id, id"
    )
    out = {}
    for agent_id, archetype, person_key, handle, display_name, team_id in cur.fetchall():
        out.setdefault(team_id, []).append({
            "agent_id": agent_id,
            "person_key": person_key or agent_id,
            "archetype": archetype or "LOCAL_BEAT_REPORTER",
            "display_name": display_name or agent_id,
            "handle": handle or ("@" + str(display_name or agent_id).replace(" ", "")),
            "role": "BEAT",
            "pool_tier": None,
        })
    return out


def national_count(rng: random.Random, tier: str, game_type: str, available: int) -> int:
    """The seeded national count for one game (the §2.4 tier ranges, capped by availability)."""
    if available <= 0:
        return 0
    if game_type == "SUPER_BOWL":
        return min(SUPER_BOWL_MAX_NATIONALS, available)
    if tier == "QUIET":
        return 1 if rng.random() < QUIET_INSIDER_CHANCE else 0
    low, high = TIER_COUNTS[tier]
    return int(_clamp(rng.randint(low, high), 0, available))


def _weighted_sample(rng: random.Random, items: list, weights: list, count: int) -> list:
    """Seeded weighted draw without replacement."""
    pool = list(zip(items, weights))
    picked = []
    for _ in range(min(count, len(pool))):
        total = sum(w for _, w in pool)
        if total <= 0:
            index = 0
        else:
            target = rng.random() * total
            running = 0.0
            index = len(pool) - 1
            for i, (_, weight) in enumerate(pool):
                running += weight
                if target < running:
                    index = i
                    break
        picked.append(pool.pop(index)[0])
    return picked


# ------------------------------------------------------------------------------------------ casting

def cast_week(cur, season: int, week: int) -> dict:
    """Cast every non-bye game of one week. Returns {game_id: Cast}.

    Games are ranked by pre-game interest (highest first). Each national attends at most one
    game a week; each team's beat reporters attend that team's presser. The result of the game
    never enters the cast.
    """
    pool = validate_pool(load_pool())
    tier_of = {arch: tier for tier, names in pool["tiers"].items() for arch in names}
    weights = pool["weights"]
    slant = _former_team_slant()
    records = _national_records(cur, pool)
    beats = _beat_reporters(cur)

    cur.execute(
        "SELECT id, season, week, game_type, game_time, home_team_id, away_team_id, projected_excitement "
        "FROM games WHERE season = %s AND week = %s AND status <> 'BYE' "
        "AND home_team_id IS NOT NULL AND away_team_id IS NOT NULL ORDER BY id",
        (season, week),
    )
    games = [
        {
            "game_id": game_id,
            "season": row_season,
            "week": row_week,
            "game_type": game_type or "REGULAR_SEASON",
            "game_time": game_time or "",
            "home_team_id": home_id,
            "away_team_id": away_id,
            "projected_excitement": projected,
        }
        for game_id, row_season, row_week, game_type, game_time, home_id, away_id, projected in cur.fetchall()
    ]

    ranked = []
    for game in games:
        result = pregame_interest(cur, game)
        ranked.append({
            **game,
            "interest": result["interest"],
            "tier": interest_tier(result["interest"], game["game_type"]),
            "inputs": result["inputs"],
        })
    ranked.sort(key=lambda g: (-g["interest"], g["game_id"]))

    used_person_keys = set()
    casts = {}
    for game in ranked:
        if game["game_type"] == "SUPER_BOWL":
            allowed_tiers = set(pool["tiers"])
        else:
            allowed_tiers = set(TIER_POOLS[game["tier"]])

        candidates = sorted(
            arch for arch, record in records.items()
            if tier_of.get(arch) in allowed_tiers and record["person_key"] not in used_person_keys
        )
        home_abbr = game["inputs"].get("home_abbreviation")
        away_abbr = game["inputs"].get("away_abbreviation")
        candidate_weights = [
            national_weight(
                arch, home_abbr, away_abbr, weights, slant=slant,
                relationship_multiplier=_relationship_multiplier(
                    cur, records[arch]["person_key"], game["home_team_id"], game["away_team_id"], weights,
                ),
            )
            for arch in candidates
        ]

        rng = random.Random(f"press:{season}:{week}:{game['game_id']}")
        count = national_count(rng, game["tier"], game["game_type"], len(candidates))
        natives = []
        for arch in _weighted_sample(rng, candidates, candidate_weights, count):
            record = records[arch]
            used_person_keys.add(record["person_key"])
            natives.append({
                "agent_id": record["agent_id"],
                "person_key": record["person_key"],
                "archetype": arch,
                "display_name": record["display_name"],
                "handle": record["handle"],
                "role": "NATIONAL",
                "pool_tier": tier_of[arch],
            })

        casts[game["game_id"]] = {
            "game_id": game["game_id"],
            "interest": game["interest"],
            "tier": game["tier"],
            "inputs": game["inputs"],
            "nationals": natives,
            "beats": {
                game["home_team_id"]: list(beats.get(game["home_team_id"], [])),
                game["away_team_id"]: list(beats.get(game["away_team_id"], [])),
            },
        }
    return casts
