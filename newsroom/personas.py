"""Writer and pundit personas (docs/PERSONALITY_PLAN.md phase 1).

Each persona in newsroom/config/agent_personas.json ("archetypes") is character, not orders: a voice, six sliders
with a fixed budget, traits from newsroom/config/persona_traits.json, views that each apply only in some
situations, a pool of optional signature moves, one want and one fear, and habits per format (ARTICLE, POST).

render_persona() turns one persona into the ROLE & PERSONA block of a prompt. It shows only the views whose
`when` tags match this piece (its article type or post kind, the season phase, and the subject team's data), offers
the signature moves as options minus the ones used in the writer's last few pieces, and lists recent headlines as
angles to avoid. Nothing here decides a number; it only chooses which stored lines reach the prompt.
"""

import json
import os
import random
import re

CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config")
PERSONAS_PATH = os.path.join(CONFIG_DIR, "agent_personas.json")
TRAITS_PATH = os.path.join(CONFIG_DIR, "persona_traits.json")

SLIDERS = ("temperament", "sociability", "confidence", "openness", "warmth", "drive")
SLIDER_BUDGET = 30
# (low end, high end) words; 4-6 is unremarkable and left out of the sentence.
SLIDER_WORDS = {
    "temperament": ("calm and even-keeled", "volatile and quick to boil over"),
    "sociability": ("private and reserved", "media-friendly and eager to engage"),
    "confidence": ("self-doubting and careful to hedge", "brash and sure of yourself"),
    "openness": ("traditional in your thinking", "experimental and open to new ideas"),
    "warmth": ("cool and detached", "warm and loyal"),
    "drive": ("content with where you are", "obsessive about the work"),
}

# Tags a view's `when` can name. Topics come from the article type or post kind and the week;
# signals come from the subject team's results (team_signals).
TOPIC_TAGS = {
    "GAME_REVIEW", "PREVIEW", "RANKINGS", "PLAYER", "QB", "SCHEME", "DRAFT", "TRADE", "ROSTER_MOVES", "CAP",
    "COACHING", "FRONT_OFFICE", "PLAYOFFS", "OFFSEASON", "AWARDS", "RECORDS", "HISTORY", "FANTASY", "INJURY",
    "HUMOR", "YOUTH", "BAD_GAME", "PERFORMANCE",
}
SIGNAL_TAGS = {
    "RECORD_OUTRUNS_DIFF", "RECORD_TRAILS_DIFF", "CLOSE_GAMES", "WIN_STREAK", "LOSING_STREAK", "CONTENDER",
    "STRUGGLING", "BLOWOUT", "UPSET",
}
ALL_TAGS = TOPIC_TAGS | SIGNAL_TAGS

# Article type id keywords -> topic tags (first match per keyword; a type can collect several).
_ARTICLE_KEYWORDS = (
    (("DRAFT", "MOCK"), ("DRAFT",)),
    (("TRADE",), ("TRADE",)),
    (("FREE_AGENCY",), ("ROSTER_MOVES", "CAP")),
    (("COACHING",), ("COACHING",)),
    (("QUARTERBACK",), ("QB", "SCHEME")),
    (("SCHEMATIC", "TAPE"), ("SCHEME", "GAME_REVIEW")),
    (("PLAYOFF", "SUPER_BOWL"), ("PLAYOFFS",)),
    (("RANKINGS", "TIERS", "TOP_10", "TOP_100", "HIERARCHY"), ("RANKINGS",)),
    (("RECAP", "AUTOPSY", "POSTGAME", "REPORT_CARD", "POSTMORTEM"), ("GAME_REVIEW",)),
    (("PREVIEW", "PREDICT", "LOOKAHEAD", "PRIMER"), ("PREVIEW",)),
    (("AWARDS", "ALL_PRO"), ("AWARDS",)),
    (("RECORD_BREAK",), ("RECORDS", "HISTORY")),
    (("HISTORIC", "LEGACY", "RETROSPECTIVE", "STAKES"), ("HISTORY",)),
    (("FRANCHISE", "BLUEPRINT", "STATE_OF_THE_UNION", "ROSTER_AUDIT"), ("FRONT_OFFICE",)),
    (("PLAYER_", "SPOTLIGHT", "X_FACTOR", "MEDIA_INTERVIEW"), ("PLAYER",)),
    (("SPOTLIGHT", "X_FACTOR", "RECORD_BREAK"), ("PERFORMANCE",)),
    (("ROOKIE", "DRAFT_CLASS", "PROGRESS_REPORT"), ("YOUTH",)),
    (("FANTASY",), ("FANTASY",)),
)

POST_KIND_TAGS = {
    "REACTION": ("GAME_REVIEW",),
    "PERFORMANCE": ("GAME_REVIEW", "PLAYER", "PERFORMANCE"),
    "PLAYER": ("PLAYER",),
    "BAD_GAME": ("GAME_REVIEW", "PLAYER", "BAD_GAME"),
    "COMPARISON": ("PLAYER", "RANKINGS"),
    "BEAT_INJURY": ("INJURY",),
    "INJURY_SHOCK": ("INJURY",),
    "STANDINGS": ("RANKINGS",),
    "PREVIEW": ("PREVIEW",),
    "FANTASY_PREVIEW": ("FANTASY", "PREVIEW"),
    "MEME": ("HUMOR",),
    "DRAFT": ("DRAFT", "HUMOR"),
    "DRAFT_BOARD": ("DRAFT",),
    "MOVE_NEWS": ("ROSTER_MOVES",),
    "BEAT_MOVE": ("ROSTER_MOVES",),
    "MOVE_REACTION": ("ROSTER_MOVES",),
    "CAP_TAKE": ("ROSTER_MOVES", "CAP"),
    "TRADE_NEWS": ("TRADE",),
    "TRADE_REACTION": ("TRADE",),
    "TRADE_RUMOR": ("TRADE",),
}

MAX_VIEWS = 3          # views shown per piece
MIN_VIEWS = 2          # topped up from the general views (empty `when`)
RECENT_ARTICLES = 3    # signature detection window, articles
RECENT_POSTS = 15      # signature detection window, posts
RECENT_HEADLINES = 5   # avoid list
SHARED_ARCHETYPES = {"LOCAL_BEAT_REPORTER", "DIE_HARD_FAN", "BANDWAGON_FAN", "FORMER_PLAYER"}


VALID_CATEGORIES = {
    "football_mind", "social", "ambition", "media", "work", "teaching", "quirks"
}
VALID_ROLES = {"writer", "gm", "player", "coach", "owner", "scout", "media", "fan", "agent"}


def load_personas_json(path: str = PERSONAS_PATH) -> dict:
    with open(path) as f:
        return json.load(f).get("archetypes", {})


def load_traits(path: str = TRAITS_PATH) -> dict:
    with open(path) as f:
        return {k: v for k, v in json.load(f).get("traits", {}).items() if not k.startswith("_")}


def validate_trait_catalogue(traits: dict = None) -> bool:
    """Validates the trait catalogue for category, role odds, prompt phrasing, and conflict symmetry."""
    if traits is None:
        traits = load_traits()
    for tid, t in traits.items():
        if tid.startswith("_"):
            continue
        if t.get("id") and t["id"] != tid:
            raise ValueError(f"Trait key '{tid}' does not match entry id '{t['id']}'")
        cat = t.get("category")
        if cat not in VALID_CATEGORIES:
            raise ValueError(f"Trait '{tid}' has invalid category '{cat}'")
        roles = t.get("roles")
        if not isinstance(roles, dict):
            raise ValueError(f"Trait '{tid}' missing roles dict")
        for r, w in roles.items():
            if r not in VALID_ROLES:
                raise ValueError(f"Trait '{tid}' has unknown role '{r}'")
            if not isinstance(w, (int, float)) or w < 0:
                raise ValueError(f"Trait '{tid}' role '{r}' weight must be non-negative number")
        prompt = t.get("prompt_text") or t.get("prompt")
        if not prompt or not prompt.startswith("tends to"):
            raise ValueError(f"Trait '{tid}' prompt must start with 'tends to'")
        conflicts = t.get("conflicts", [])
        if not isinstance(conflicts, list):
            raise ValueError(f"Trait '{tid}' conflicts must be a list")
        for c in conflicts:
            if c == tid:
                raise ValueError(f"Trait '{tid}' cannot conflict with itself")
            if c not in traits:
                raise ValueError(f"Trait '{tid}' conflicts with unknown trait '{c}'")
            if tid not in traits[c].get("conflicts", []):
                raise ValueError(f"Conflict asymmetry: '{tid}' lists '{c}', but '{c}' does not list '{tid}'")
        when = t.get("when")
        if when is not None:
            if not isinstance(when, (list, set, tuple)):
                raise ValueError(f"Trait '{tid}' when must be a list/set/tuple")
            diff = set(when) - ALL_TAGS
            if diff:
                raise ValueError(f"Trait '{tid}' has invalid when tags: {diff}")
    return True


def validate_slider_budget(sliders: dict) -> bool:
    """Validates that a persona's 6 sliders are in [0, 10] and sum to exactly SLIDER_BUDGET (30)."""
    if not isinstance(sliders, dict):
        raise ValueError("Sliders must be a dictionary")
    diff = set(SLIDERS) - set(sliders.keys())
    if diff:
        raise ValueError(f"Missing sliders: {diff}")
    extra = set(sliders.keys()) - set(SLIDERS)
    if extra:
        raise ValueError(f"Unknown sliders: {extra}")
    for s in SLIDERS:
        v = sliders[s]
        if not isinstance(v, int) or isinstance(v, bool):
            raise ValueError(f"Slider '{s}' must be an integer, got {type(v).__name__}")
        if not (0 <= v <= 10):
            raise ValueError(f"Slider '{s}' value {v} out of bounds [0, 10]")
    total = sum(sliders[s] for s in SLIDERS)
    if total != SLIDER_BUDGET:
        raise ValueError(f"Slider total {total} does not match budget {SLIDER_BUDGET}")
    return True


def validate_persona_traits(traits_list: list, traits: dict = None) -> bool:
    """Validates that persona traits are 3-5, have no duplicates, max 2 per category, and no conflicts."""
    if traits is None:
        traits = load_traits()
    if not isinstance(traits_list, (list, tuple, set)):
        raise ValueError("Traits must be a list")
    if not (3 <= len(traits_list) <= 5):
        raise ValueError(f"Persona must have between 3 and 5 traits, got {len(traits_list)}")
    if len(set(traits_list)) != len(traits_list):
        raise ValueError("Duplicate traits in persona")
    cats = {}
    for t in traits_list:
        if t not in traits:
            raise ValueError(f"Unknown trait '{t}'")
        cat = traits[t].get("category")
        cats[cat] = cats.get(cat, 0) + 1
        if cats[cat] > 2:
            raise ValueError(f"Too many traits from category '{cat}' (max 2 allowed)")
        conflicts = set(traits[t].get("conflicts", []))
        clashes = conflicts & set(traits_list)
        if clashes:
            raise ValueError(f"Trait '{t}' conflicts with {clashes}")
    return True


def seed_sliders(persona_type: str, persona_id: str) -> dict:
    """Deterministically generates 6 sliders that sum to 30 points within [0, 10]."""
    seed = f"{persona_type}:{persona_id}"
    rng = random.Random(seed)
    sliders = {s: 5 for s in SLIDERS}
    keys = list(SLIDERS)
    steps = rng.randint(15, 30)
    for _ in range(steps):
        a, b = rng.sample(keys, 2)
        if sliders[a] < 10 and sliders[b] > 0:
            sliders[a] += 1
            sliders[b] -= 1
    return sliders


def seed_traits(persona_type: str, persona_id: str, count: int = 4, traits: dict = None) -> list:
    """Deterministically selects 3-5 traits for an agent role, max 2 per category, without conflicts."""
    if traits is None:
        traits = load_traits()
    seed = f"{persona_type}:{persona_id}"
    rng = random.Random(seed)

    role = persona_type.lower()
    candidates = []
    for tid, t in traits.items():
        w = t.get("roles", {}).get(role, 0)
        if w > 0:
            candidates.append((tid, t, w))

    if not candidates:
        candidates = [(tid, t, 1) for tid, t in traits.items()]

    shuffled = list(candidates)
    rng.shuffle(shuffled)
    shuffled.sort(key=lambda item: -(rng.random() ** (1.0 / max(0.1, item[2]))))

    chosen = []
    category_counts = {}
    for tid, t, _ in shuffled:
        cat = t.get("category")
        if category_counts.get(cat, 0) >= 2:
            continue
        conflicts = set(t.get("conflicts", []))
        if any(c in chosen for c in conflicts):
            continue
        if any(tid in traits[c].get("conflicts", []) for c in chosen):
            continue
        chosen.append(tid)
        category_counts[cat] = category_counts.get(cat, 0) + 1
        if len(chosen) >= count:
            break

    return chosen


def seed_persona(persona_type: str, persona_id: str, count: int = 4, traits: dict = None) -> dict:
    """Seeds a full persona dict deterministically."""
    sliders = seed_sliders(persona_type, persona_id)
    t_list = seed_traits(persona_type, persona_id, count=count, traits=traits)
    return {
        "persona_type": persona_type,
        "persona_id": persona_id,
        "sliders": sliders,
        "traits": t_list,
        "views": [],
        "relationships": []
    }


def get_persona(persona_type: str, persona_id: str, cur=None) -> dict:
    """Reads a persona from the database tables, falling back to JSON or deterministic seeding."""
    if cur is not None:
        try:
            cur.execute("""
                SELECT temperament, sociability, confidence, openness, warmth, drive
                FROM persona_sliders WHERE persona_type = %s AND persona_id = %s
            """, (persona_type, persona_id))
            row = cur.fetchone()
            if row:
                sliders = {
                    "temperament": row[0], "sociability": row[1], "confidence": row[2],
                    "openness": row[3], "warmth": row[4], "drive": row[5]
                }
                cur.execute("""
                    SELECT trait_id FROM persona_traits
                    WHERE persona_type = %s AND persona_id = %s AND active = TRUE ORDER BY id
                """, (persona_type, persona_id))
                t_list = [r[0] for r in cur.fetchall()]

                cur.execute("""
                    SELECT view_text, trigger_condition, subject_tags FROM persona_views
                    WHERE persona_type = %s AND persona_id = %s ORDER BY id
                """, (persona_type, persona_id))
                v_list = [{"text": r[0], "trigger": r[1], "when": r[2] or []} for r in cur.fetchall()]

                cur.execute("""
                    SELECT target_type, target_id, trust, note, tags FROM persona_relationships
                    WHERE persona_type = %s AND persona_id = %s ORDER BY id
                """, (persona_type, persona_id))
                r_list = [{"target_type": r[0], "target_id": r[1], "trust": float(r[2]), "note": r[3], "tags": r[4] or []}
                          for r in cur.fetchall()]

                base = {}
                if persona_type == "writer":
                    json_data = load_personas_json()
                    if persona_id in json_data:
                        base = dict(json_data[persona_id])

                base.update({
                    "persona_type": persona_type,
                    "persona_id": persona_id,
                    "sliders": sliders,
                    "traits": t_list,
                    "views": v_list,
                    "relationships": r_list
                })
                return base
        except Exception:
            pass

    # Fallback
    if persona_type == "writer":
        json_data = load_personas_json()
        if persona_id in json_data:
            return dict(json_data[persona_id])
    return seed_persona(persona_type, persona_id)


def save_persona(persona_type: str, persona_id: str, sliders: dict = None,
                 traits: list = None, views: list = None, cur=None) -> dict:
    """Saves persona sliders, traits, and views to the database."""
    if cur is None:
        raise ValueError("Database cursor required to save persona")

    if sliders is not None:
        validate_slider_budget(sliders)
        cur.execute("""
            INSERT INTO persona_sliders (persona_type, persona_id, temperament, sociability,
                                         confidence, openness, warmth, drive, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (persona_type, persona_id) DO UPDATE SET
                temperament = EXCLUDED.temperament,
                sociability = EXCLUDED.sociability,
                confidence = EXCLUDED.confidence,
                openness = EXCLUDED.openness,
                warmth = EXCLUDED.warmth,
                drive = EXCLUDED.drive,
                updated_at = now()
        """, (persona_type, persona_id, sliders["temperament"], sliders["sociability"],
              sliders["confidence"], sliders["openness"], sliders["warmth"], sliders["drive"]))

    if traits is not None:
        trait_ids = [t["trait_id"] if isinstance(t, dict) else t for t in traits]
        validate_persona_traits(trait_ids)
        cur.execute("DELETE FROM persona_traits WHERE persona_type = %s AND persona_id = %s",
                    (persona_type, persona_id))
        for item in traits:
            if isinstance(item, dict):
                tid = item["trait_id"]
                active = item.get("active", True)
                notes = item.get("notes")
            else:
                tid = item
                active = True
                notes = None
            cur.execute("""
                INSERT INTO persona_traits (persona_type, persona_id, trait_id, active, notes)
                VALUES (%s, %s, %s, %s, %s)
            """, (persona_type, persona_id, tid, active, notes))

    if views is not None:
        cur.execute("DELETE FROM persona_views WHERE persona_type = %s AND persona_id = %s",
                    (persona_type, persona_id))
        for v in views:
            cur.execute("""
                INSERT INTO persona_views (persona_type, persona_id, view_text, trigger_condition, subject_tags)
                VALUES (%s, %s, %s, %s, %s)
            """, (persona_type, persona_id, v.get("text") or v.get("view_text"),
                  v.get("trigger") or v.get("trigger_condition", ""),
                  v.get("when") or v.get("subject_tags", [])))

    return get_persona(persona_type, persona_id, cur=cur)


def seed_writers_to_db(cur, personas_data: dict = None) -> int:
    """Seeds writer personas from agent_personas.json into the DB tables, preserving identities."""
    if personas_data is None:
        personas_data = load_personas_json()
    count = 0
    for k, p in personas_data.items():
        s = p.get("sliders", {})
        if set(s.keys()) == set(SLIDERS) and sum(s.values()) == SLIDER_BUDGET:
            cur.execute("""
                INSERT INTO persona_sliders (persona_type, persona_id, temperament, sociability,
                                             confidence, openness, warmth, drive, updated_at)
                VALUES ('writer', %s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (persona_type, persona_id) DO UPDATE SET
                    temperament = EXCLUDED.temperament,
                    sociability = EXCLUDED.sociability,
                    confidence = EXCLUDED.confidence,
                    openness = EXCLUDED.openness,
                    warmth = EXCLUDED.warmth,
                    drive = EXCLUDED.drive,
                    updated_at = now()
            """, (k, s["temperament"], s["sociability"], s["confidence"],
                  s["openness"], s["warmth"], s["drive"]))
            count += 1

        cur.execute("DELETE FROM persona_traits WHERE persona_type = 'writer' AND persona_id = %s", (k,))
        for t in p.get("traits", []):
            cur.execute("""
                INSERT INTO persona_traits (persona_type, persona_id, trait_id, active)
                VALUES ('writer', %s, %s, TRUE)
                ON CONFLICT (persona_type, persona_id, trait_id) DO UPDATE SET active = TRUE
            """, (k, t))

        cur.execute("DELETE FROM persona_views WHERE persona_type = 'writer' AND persona_id = %s", (k,))
        for v in p.get("views", []):
            cur.execute("""
                INSERT INTO persona_views (persona_type, persona_id, view_text, trigger_condition, subject_tags)
                VALUES ('writer', %s, %s, '', %s)
            """, (k, v["text"], v.get("when", [])))
    return count


def seed_gms_to_db(cur, traits_catalogue: dict = None) -> int:
    """Seeds GM personas into persona_sliders and persona_traits from gm_profiles."""
    if traits_catalogue is None:
        traits_catalogue = load_traits()
    cur.execute("""
        SELECT id, team_id, gm_name, aggressiveness, risk_appetite, pick_affinity, youth_bias
        FROM gm_profiles WHERE end_season IS NULL
    """)
    rows = cur.fetchall()
    count = 0
    for r in rows:
        gm_id = str(r[0])
        sliders = seed_sliders("gm", gm_id)
        traits = seed_traits("gm", gm_id, count=4, traits=traits_catalogue)

        cur.execute("""
            INSERT INTO persona_sliders (persona_type, persona_id, temperament, sociability,
                                         confidence, openness, warmth, drive, updated_at)
            VALUES ('gm', %s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (persona_type, persona_id) DO UPDATE SET
                temperament = EXCLUDED.temperament,
                sociability = EXCLUDED.sociability,
                confidence = EXCLUDED.confidence,
                openness = EXCLUDED.openness,
                warmth = EXCLUDED.warmth,
                drive = EXCLUDED.drive,
                updated_at = now()
        """, (gm_id, sliders["temperament"], sliders["sociability"], sliders["confidence"],
              sliders["openness"], sliders["warmth"], sliders["drive"]))

        cur.execute("DELETE FROM persona_traits WHERE persona_type = 'gm' AND persona_id = %s", (gm_id,))
        for t in traits:
            cur.execute("""
                INSERT INTO persona_traits (persona_type, persona_id, trait_id, active)
                VALUES ('gm', %s, %s, TRUE)
                ON CONFLICT (persona_type, persona_id, trait_id) DO UPDATE SET active = TRUE
            """, (gm_id, t))
        count += 1
    return count


def load_personas(path: str = PERSONAS_PATH, cur=None) -> dict:
    """Loads personas from database tables with JSON fallback."""
    json_personas = load_personas_json(path)
    if cur is None:
        return json_personas
    try:
        cur.execute("""
            SELECT persona_id, temperament, sociability, confidence, openness, warmth, drive
            FROM persona_sliders WHERE persona_type = 'writer'
        """)
        slider_rows = cur.fetchall()
        if not slider_rows:
            return json_personas

        cur.execute("""
            SELECT persona_id, trait_id FROM persona_traits
            WHERE persona_type = 'writer' AND active = TRUE ORDER BY id
        """)
        trait_rows = cur.fetchall()
        traits_by_id = {}
        for pid, tid in trait_rows:
            traits_by_id.setdefault(pid, []).append(tid)

        cur.execute("""
            SELECT persona_id, view_text, subject_tags FROM persona_views
            WHERE persona_type = 'writer' ORDER BY id
        """)
        view_rows = cur.fetchall()
        views_by_id = {}
        for pid, vt, tags in view_rows:
            views_by_id.setdefault(pid, []).append({"text": vt, "when": tags or []})

        out = {}
        for r in slider_rows:
            pid = r[0]
            base = dict(json_personas.get(pid, {}))
            base["sliders"] = {
                "temperament": r[1], "sociability": r[2], "confidence": r[3],
                "openness": r[4], "warmth": r[5], "drive": r[6]
            }
            if pid in traits_by_id:
                base["traits"] = traits_by_id[pid]
            if pid in views_by_id:
                base["views"] = views_by_id[pid]
            out[pid] = base

        for k, v in json_personas.items():
            if k not in out:
                out[k] = v
        return out
    except Exception:
        return json_personas



# --------------------------------------------------------------------------------------------- context tags ---
def article_tags(article_type: str = None) -> set:
    at = (article_type or "").upper()
    tags = set()
    for keywords, out in _ARTICLE_KEYWORDS:
        if any(k in at for k in keywords):
            tags.update(out)
    if at.startswith("OFFSEASON_"):
        tags.add("OFFSEASON")
    return tags


def post_tags(post_kind: str = None, position: str = None) -> set:
    tags = set(POST_KIND_TAGS.get((post_kind or "").upper(), ()))
    if (position or "").upper() == "QB":
        tags.add("QB")
    return tags


def phase_tags(week: int = None) -> set:
    """Weeks 19-23 are the playoffs (the Super Bowl is week 23); later weeks are the offseason."""
    if week is None:
        return set()
    if week > 23:
        return {"OFFSEASON"}
    if week >= 19:
        return {"PLAYOFFS"}
    return set()


def signals_from_games(games: list, team_id: int = None) -> set:
    """Signal tags from completed regular-season games [(week, home_id, away_id, home_score, away_score)].

    With team_id: that team's signals. Without: league-level signals, held to stricter thresholds so they
    fire only when some team stands out (a national piece can pick its own subject)."""
    from engines.team_efficiency_engine import pythagorean_wins

    games = sorted(games, key=lambda g: g[0])
    by_team = {}
    for wk, h, a, hs, as_ in games:
        by_team.setdefault(h, []).append((wk, a, hs, as_))
        by_team.setdefault(a, []).append((wk, h, as_, hs))

    def profile(tid, upto=None):
        rows = [r for r in by_team.get(tid, []) if upto is None or r[0] < upto]
        wins = sum(1.0 if pf > pa else 0.5 if pf == pa else 0.0 for _, _, pf, pa in rows)
        return rows, wins

    def team(tid, luck_cut=1.0, streak_cut=3):
        rows, wins = profile(tid)
        n = len(rows)
        tags = set()
        if n == 0:
            return tags
        pf, pa = sum(r[2] for r in rows), sum(r[3] for r in rows)
        if n >= 4:
            luck = wins - pythagorean_wins(pf, pa, n)
            if luck >= luck_cut:
                tags.add("RECORD_OUTRUNS_DIFF")
            elif luck <= -luck_cut:
                tags.add("RECORD_TRAILS_DIFF")
            close = [r for r in rows if 0 < abs(r[2] - r[3]) <= 7]
            close_wins = sum(1 for r in close if r[2] > r[3])
            if len(close) >= 4 and (close_wins >= 0.75 * len(close) or close_wins <= 0.25 * len(close)):
                tags.add("CLOSE_GAMES")     # a lopsided one-score record: luck or late-game execution
            pct = wins / n
            if pct >= 0.65:
                tags.add("CONTENDER")
            elif pct <= 0.35:
                tags.add("STRUGGLING")
        streak, last = 0, None
        for _, _, f, a in reversed(rows):
            res = "W" if f > a else "L" if f < a else "T"
            if last is None:
                last = res
            if res != last or res == "T":
                break
            streak += 1
        if last == "W" and streak >= streak_cut:
            tags.add("WIN_STREAK")
        elif last == "L" and streak >= streak_cut:
            tags.add("LOSING_STREAK")
        wk, opp, f, a = rows[-1]
        if abs(f - a) >= 17:
            tags.add("BLOWOUT")
        mine, opp_rows = profile(tid, wk), profile(opp, wk)
        if len(mine[0]) >= 3 and len(opp_rows[0]) >= 3:
            gap = mine[1] / len(mine[0]) - opp_rows[1] / len(opp_rows[0])
            if (f < a and gap >= 0.25) or (f > a and gap <= -0.25):
                tags.add("UPSET")
        return tags

    if team_id is not None:
        return team(team_id)
    league = set()
    for tid in by_team:
        league |= team(tid, luck_cut=2.0, streak_cut=5) & {"RECORD_OUTRUNS_DIFF", "RECORD_TRAILS_DIFF", "WIN_STREAK", "LOSING_STREAK"}
    return league


def team_signals(cur, season: int, week: int, team_id: int = None) -> set:
    """Signal tags from this season's completed regular-season games before `week`."""
    cur.execute("""
        SELECT week, home_team_id, away_team_id, home_score, away_score FROM games
        WHERE season = %s AND week < %s AND status = 'COMPLETED'
          AND COALESCE(game_type, 'REGULAR_SEASON') = 'REGULAR_SEASON'
          AND home_score IS NOT NULL AND away_score IS NOT NULL
    """, (season, week))
    return signals_from_games(cur.fetchall(), team_id)


def recent_work(cur, agent_id: str, season: int, week: int, fmt: str, archetype: str = None) -> tuple:
    """(recent texts for signature detection, recent headlines) from the writer's own pieces before this week.

    Articles saved before agent_id was stored on news_feed (2026-10-28) are matched by archetype, except for
    archetypes that many agents share (beat reporters, fans)."""
    if not agent_id:
        return [], []
    content_type = "ARTICLE" if fmt == "ARTICLE" else "TWEET"
    limit = RECENT_ARTICLES if fmt == "ARTICLE" else RECENT_POSTS
    by_archetype = archetype if archetype and archetype not in SHARED_ARCHETYPES else None
    cur.execute("""
        SELECT headline, content FROM news_feed
        WHERE content_type = %s AND (season < %s OR (season = %s AND week < %s))
          AND (agent_id = %s OR (agent_id IS NULL AND %s::text IS NOT NULL AND persona = %s))
        ORDER BY season DESC, week DESC, id DESC LIMIT %s
    """, (content_type, season, season, week, agent_id, by_archetype, by_archetype, max(limit, RECENT_HEADLINES)))
    rows = cur.fetchall()
    texts = [c for _, c in rows[:limit] if c]
    headlines = [h for h, _ in rows[:RECENT_HEADLINES] if h] if fmt == "ARTICLE" else []
    return texts, headlines


# ---------------------------------------------------------------------------------------------- rendering ---
def slider_sentence(sliders: dict) -> str:
    words = []
    for s in SLIDERS:
        v = sliders.get(s)
        if v is None:
            continue
        if v <= 3:
            words.append(SLIDER_WORDS[s][0])
        elif v >= 7:
            words.append(SLIDER_WORDS[s][1])
    if not words:
        return ""
    body = words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]
    return f"By nature you are {body}."


def select_views(views: list, tags: set, rng: random.Random) -> list:
    matched = []
    for v in views:
        when = set(v.get("when") or ())
        if when and when & tags:
            matched.append((len(when & tags), rng.random(), v["text"]))
    chosen = [t for _, _, t in sorted(matched, key=lambda m: (-m[0], m[1]))[:MAX_VIEWS]]
    general = [v["text"] for v in views if not v.get("when")]
    rng.shuffle(general)
    while len(chosen) < MIN_VIEWS and general:
        chosen.append(general.pop())
    return chosen


def used_signatures(signatures: list, recent_texts: list) -> set:
    used = set()
    for i, s in enumerate(signatures):
        pat = s.get("detect")
        if pat and any(re.search(pat, t or "", re.I) for t in recent_texts):
            used.add(i)
    return used


def render_persona(persona, fmt: str = "ARTICLE", tags: set = frozenset(), seed: str = "",
                   recent_texts: list = (), recent_headlines: list = (), traits: dict = None) -> str:
    """The ROLE & PERSONA block for one piece. fmt is ARTICLE or POST. A plain string persona passes through."""
    if isinstance(persona, str):
        return persona
    if traits is None:
        traits = load_traits()
    rng = random.Random(f"persona:{seed}")
    tags = set(tags)
    out = [persona["voice"].strip()]

    sentence = slider_sentence(persona.get("sliders", {}))
    if sentence:
        out.append(sentence)

    lines = []
    for t in persona.get("traits", []):
        entry = traits.get(t)
        if not entry:
            continue
        when = set(entry.get("when") or ())
        if when and not (when & tags):
            continue
        lines.append(f"- {entry['label']}: {entry['prompt']}")
    if lines:
        out.append("TENDENCIES (nudges, not rules):\n" + "\n".join(lines))

    habits = (persona.get("formats") or {}).get(fmt)
    if habits:
        out.append(f"HOW YOU WRITE {'A COLUMN' if fmt == 'ARTICLE' else 'A POST'}:\n{habits.strip()}")

    views = select_views(persona.get("views", []), tags, rng)
    if views:
        out.append("VIEWS OF YOURS THAT MAY BEAR ON THIS PIECE (use one only where the evidence supports it; "
                   "it is fine to use none):\n" + "\n".join(f"- {v}" for v in views))

    sigs = persona.get("signatures", [])
    used = used_signatures(sigs, list(recent_texts))
    offered = [s["text"] for i, s in enumerate(sigs) if i not in used]
    if offered:
        out.append("SIGNATURE MOVES (optional; at most one, and only if it fits naturally):\n"
                   + "\n".join(f"- {s}" for s in offered))
    if used:
        out.append("USED IN YOUR LAST FEW PIECES (leave these out this time):\n"
                   + "\n".join(f"- {sigs[i]['text']}" for i in sorted(used)))

    if persona.get("want") or persona.get("fear"):
        out.append("IN THE BACKGROUND (motivation only; do not state it): "
                   f"you want {persona.get('want', '').rstrip('.')}; you fear {persona.get('fear', '').rstrip('.')}.")

    if recent_headlines:
        out.append("YOUR RECENT PIECES (find a different angle and thesis this time):\n"
                   + "\n".join(f"- {h}" for h in recent_headlines))
    return "\n\n".join(out)


def build_persona_block(cur, persona, *, agent_id: str = None, archetype: str = None, season: int = None, week: int = None,
                        fmt: str = "ARTICLE", article_type: str = None, post_kind: str = None,
                        position: str = None, team_id: int = None, subject_key: str = "") -> str:
    """render_persona with tags and recent work from the database. Database trouble only drops the
    data-driven parts (signals, avoid list); the persona itself still renders."""
    if isinstance(persona, str):
        return persona
    tags = article_tags(article_type) | post_tags(post_kind, position) | phase_tags(week)
    texts, headlines = [], []
    if cur is not None and season is not None and week is not None:
        try:
            tags |= _guarded(cur, lambda: team_signals(cur, season, week, team_id))
        except Exception as e:
            print(f"⚠️  [PERSONA SIGNALS UNAVAILABLE] {agent_id}: {e}")
        try:
            texts, headlines = _guarded(cur, lambda: recent_work(cur, agent_id, season, week, fmt, archetype))
        except Exception as e:
            print(f"⚠️  [PERSONA RECENT WORK UNAVAILABLE] {agent_id}: {e}")
    seed = f"{agent_id}:{season}:{week}:{fmt}:{article_type or post_kind}:{team_id}:{subject_key}"
    return render_persona(persona, fmt, tags, seed, texts, headlines)


def _guarded(cur, fn):
    """Runs fn inside a savepoint so a failed read doesn't abort the caller's transaction (the Agent Wire
    shares one cursor across a whole run). In autocommit mode there's nothing to protect."""
    try:
        cur.execute("SAVEPOINT persona_ctx")
    except Exception:
        return fn()
    try:
        result = fn()
    except Exception:
        cur.execute("ROLLBACK TO SAVEPOINT persona_ctx")
        raise
    cur.execute("RELEASE SAVEPOINT persona_ctx")
    return result
