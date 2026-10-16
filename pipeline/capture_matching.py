"""
Matching captured rows to players (docs/PIPELINE_MIGRATION.md, phase 3). Pure functions, no
database access, so they can be unit-tested; pipeline/load_capture.py does the queries.

Names come in three spellings: the roster list and stats tables show "G.Crawford", the database
stores player_name "G. Crawford" and full_name "George Crawford", and the card has first and last
name separately. Everything is compared through name_key().
"""
import re

# suffixes: " III", " Jr." - and "Hilljr." where OCR glued Jr./Sr. on (Titans, Oct 14)
RESULT_RE = re.compile(r"^([A-Z]{2,3}) ?(\d+) ?- ?(\d+) ?([A-Z]{2,3})$")    # "DET 38 - 24 NO"

# games.week of the postseason rounds (docs/EDITORIAL_MATRIX.md: 22 is the Super Bowl bye)
POST_WEEKS = {
    "WILDCARD": 19,
    "WILDCARDROUND": 19,
    "DIVISIONAL": 20,
    "DIVPLAYOFF": 20,
    "DIVPLAYOFFS": 20,
    "DIVISIONALPLAYOFF": 20,
    "DIVISIONALPLAYOFFS": 20,
    "DIVISIONALROUND": 20,
    "CONFERENCE": 21,
    "CONFPLAYOFF": 21,
    "CONFPLAYOFFS": 21,
    "CONFCHAMP": 21,
    "CONFCHAMPIONSHIP": 21,
    "CONFCHAMPIONSHIPS": 21,
    "CONFERENCEPLAYOFF": 21,
    "CONFERENCEPLAYOFFS": 21,
    "CONFERENCECHAMP": 21,
    "CONFERENCECHAMPIONSHIP": 21,
    "CONFERENCECHAMPIONSHIPS": 21,
    "CONFERENCEROUND": 21,
    "PROBOWL": 22,
    "SUPERBOWL": 23,
    "SUPERBOWLROUND": 23,
}


def db_week(label):
    """games.week for a Madden week label: 'SEASON WEEK 17' and the schedule CSV's 'Week 17'
    give 17, 'POST SEASON WILD CARD' 19 ... 'POST SEASON SUPER BOWL' 23. Also handles schedule
    table labels like 'Div. Playoff' (20) and 'Conf. Playoff' (21). Preseason and
    unreadable labels give None."""
    t = re.sub(r"\s+", " ", (label or "").upper().strip())
    m = re.match(r"^(?:SEASON )?WEEK (\d{1,2})$", t)
    if m:
        return int(m.group(1))
    clean = re.sub(r"^POST ?SEASON ", "", t)
    clean = re.sub(r"[^A-Z]", "", clean)
    res = POST_WEEKS.get(clean)
    if res is not None:
        return res
    if clean.startswith(("DIVPLAYOFF", "DIVISION")):
        return 20
    if clean.startswith(("CONFPLAYOFF", "CONFCHAMP", "CONFERENCE")):
        return 21
    return None

SUFFIX_RE = re.compile(r"(?:\s+(?:JR\.?|SR\.?|II|III|IV|V)|(?<=[a-z])(?:JR|SR)\.?)$", re.I)


def name_key(name):
    """The list-name key: first initial + last name, upper case, letters only, suffix dropped.
    'G.Crawford', 'G. Crawford', 'George Crawford' -> 'GCRAWFORD'; 'T.J. Parker' -> 'TPARKER'."""
    name = SUFFIX_RE.sub("", (name or "").strip())
    head = name.split(" ", 1)[0]
    if "." in head and not head.endswith("."):        # "P.Van Buren", "G.Crawford": initial before the dot
        first, rest = name.split(".", 1)
    elif " " in name:                                 # "G. Crawford", "T.J. Parker", "George Crawford"
        first, rest = name.split(" ", 1)
    else:
        first, rest = "", name
    return re.sub(r"[^A-Z]", "", (first[:1] + rest).upper())


def full_key(name):
    """'George Crawford' / 'Walter Nolen III' -> 'GEORGECRAWFORD', 'WALTERNOLEN'."""
    return re.sub(r"[^A-Z]", "", SUFFIX_RE.sub("", (name or "").strip()).upper())


def all_caps(name):
    """True for a spelling with no lower-case letters ('JERRY RAYFORD', 'O. COOPER JR.'): the old video
    scanner's OCR, which the roster load replaces with the card's casing."""
    letters = re.sub(r"[^A-Za-z]", "", name or "")
    return len(letters) >= 4 and letters.isupper()


def name_case(name):
    """Fix title-casing from OCR: 'Nolen Iii' -> 'Nolen III', 'Hill Jr' -> 'Hill Jr.', 'Cj' -> 'C.J.'
    when the capture lost the dots (two-letter all-consonant first names), 'Mccain' -> 'McCain'."""
    words = []
    for w in (name or "").split():
        if re.fullmatch(r"(?i)(ii|iii|iv|v)", w):
            w = w.upper()
        elif re.fullmatch(r"(?i)(jr|sr)\.?", w):
            w = w[:2].title() + "."
        elif re.fullmatch(r"(?i)[bcdfghjklmnpqrstvwxz]{2}", w):
            w = ".".join(w.upper()) + "."
        elif re.fullmatch(r"(?i)mc[a-z]{2,}", w):
            w = "Mc" + w[2:].title()
        words.append(w)
    return " ".join(words)


def db_player_name(first, last):
    """The database's player_name style: 'G. Crawford' (suffix kept as Madden shows it)."""
    return f"{first.strip()[:1].upper()}. {last.strip()}"


def num(value):
    """A captured cell -> int or None ('5,121' -> 5121, '' -> None). '60%' -> 60."""
    v = str(value or "").strip().replace(",", "").rstrip("%")
    if v in ("", "-", "--"):
        return None
    try:
        f = float(v)
    except ValueError:
        raise ValueError(f"not a number: {value!r}")
    if not f.is_integer():
        raise ValueError(f"not a whole number: {value!r}")
    return int(f)


def dec(value):
    """A captured cell -> float or None ('97.7', '60%' -> 60.0, '' -> None)."""
    v = str(value or "").strip().replace(",", "").rstrip("%")
    if v in ("", "-", "--"):
        return None
    try:
        return float(v)
    except ValueError:
        raise ValueError(f"not a number: {value!r}")


ROSTER_TESTS = [
    ("list name + team + position", lambda card, c: c["name_key"] == card["name_key"] and c["team_id"] == card["team_id"]
     and c["position"] == card["position"]),
    ("list name + team", lambda card, c: c["name_key"] == card["name_key"] and c["team_id"] == card["team_id"]),
    ("full name + position", lambda card, c: _consistent(c) and c["full_key"] == card["full_key"]
     and c["position"] == card["position"]),
    ("list name + position", lambda card, c: c["name_key"] == card["name_key"] and c["position"] == card["position"]),
    ("full name", lambda card, c: _consistent(c) and c["full_key"] == card["full_key"]),
]


def _consistent(c):
    return bool(c["full_key"]) and name_key_from_full(c["full_key"], c["name_key"])


def match_roster(cards, existing):
    """Match every roster card to an existing player, in passes over the whole roster.

    cards: [{"full_key", "name_key", "team_id", "position", "number"}]
    existing: [{"id", "full_key", "name_key", "team_id", "position", "number"}] (team/position
        from player_seasons for the season, may be None)
    The stats tables join on player_name, so the list name (player_name) decides first; some
    existing rows carry another player's full_name (144 rows on Oct 14, e.g. "D. Moyer" / "Armand
    Membou"), so a full-name test only counts where the row's two names agree. Each test runs for
    every card before the next, weaker one, and a row claimed by one card can't be claimed by
    another (two Manny Santos, Chargers and Ravens, one row in the database). Ties are broken by a
    full name the row agrees with, then the number.

    Returns (matches {card index: (player id, how)}, new [card index], ambiguous [message]).
    """
    claimed, matches, ambiguous = set(), {}, []
    by_key = {}
    for e in existing:
        by_key.setdefault(e["name_key"], []).append(e)
        if e["full_key"]:
            by_key.setdefault(("F", e["full_key"]), []).append(e)

    def cands(card):
        seen = {}
        for e in by_key.get(card["name_key"], []) + by_key.get(("F", card["full_key"]), []):
            seen[e["id"]] = e
        return list(seen.values())

    def best(card, hits):
        if len(hits) <= 1:
            return hits
        for narrow in (lambda c: _consistent(c) and c["full_key"] == card["full_key"],
                       lambda c: card["number"] is not None and c["number"] == card["number"]):
            n = [c for c in hits if narrow(c)]
            if len(n) == 1:
                return n
        return hits

    for how, test in ROSTER_TESTS:
        wants = {}                                   # candidate id -> card indexes that point at it
        for i, card in enumerate(cards):
            if i in matches:
                continue
            hits = best(card, [c for c in cands(card) if c["id"] not in claimed and test(card, c)])
            if len(hits) == 1:
                wants.setdefault(hits[0]["id"], []).append(i)
            elif len(hits) > 1 and how == ROSTER_TESTS[-1][0]:
                ambiguous.append(f"card {i}: {len(hits)} players by {how}")
        for pid, idxs in wants.items():
            if len(idxs) > 1:                        # several cards want one row: the full name decides
                row = next(e for e in existing if e["id"] == pid)
                idxs = [i for i in idxs if _consistent(row) and row["full_key"] == cards[i]["full_key"]] or idxs
            if len(idxs) == 1:
                matches[idxs[0]] = (pid, how)
                claimed.add(pid)
    new = [i for i in range(len(cards)) if i not in matches]
    return matches, new, ambiguous


def name_key_from_full(full_key_value, list_key):
    """Does a stored full name belong to the stored list name? ('GEORGECRAWFORD', 'GCRAWFORD')."""
    return bool(full_key_value) and full_key_value[:1] == list_key[:1] and full_key_value.endswith(list_key[1:])


def assign_by_history(rows, candidates, keys):
    """Season Stats rows have name + position but no team. When several players share them,
    assign each row to the player whose previous totals fit: every stat at least as high as
    before (totals only go up) and the smallest total increase. Each player takes at most one row.

    rows: list of {stat: value}; candidates: {player_id: {stat: previous value}} (missing = no
    history). Returns {row index: player_id} for rows it could settle, or None when it can't
    (no history, or two assignments fit equally well) - the loader then stops and asks.
    """
    import itertools
    ids = list(candidates)
    if len(rows) > len(ids) or any(not candidates[i] for i in ids):
        return None

    def cost(row, prev):
        diffs = [(row.get(k) or 0) - (prev.get(k) or 0) for k in keys]
        if any(d < 0 for d in diffs):
            return None                      # a total went down: not this player
        return sum(diffs)

    best, best_cost, tie = None, None, False
    for perm in itertools.permutations(ids, len(rows)):
        costs = [cost(r, candidates[p]) for r, p in zip(rows, perm)]
        if any(c is None for c in costs):
            continue
        total = sum(costs)
        if best_cost is None or total < best_cost:
            best, best_cost, tie = perm, total, False
        elif total == best_cost:
            tie = True
    if best is None or tie:
        return None
    return dict(enumerate(best))
