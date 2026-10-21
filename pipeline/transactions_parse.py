"""
League > LEAGUE TRANSACTIONS rows (docs/team_management/02_LEAGUE_TRANSACTIONS_PLAN.md): pure parsing, no
database and no Quartz/Vision, so the capture, the extractor, the loader and the tests can all share it.

A row is a heading and a line of text (newest first on screen):
    2036 SEASON - WEEK 4
    JAGUARS SIGNED FREE AGENT (WR) DEVONTA TAYLOR TO 1 YEAR, $3.93M CONTRACT
    JAGUARS RELEASE (WR) JULIUS SLATON

Headings and texts come from Apple Vision OCR, so the digit fixes of madden_capture.clean_week (T/I/L -> 1,
O -> 0) apply. Trade, re-sign and extension wording isn't confirmed at the console yet (plan §1, facts 7-8):
those grammars are tolerant and anything they can't place is flagged, never dropped.
"""
import re

from pipeline.capture_matching import db_week, full_key

TEAM_NICKNAMES = frozenset({
    "49ERS", "BEARS", "BENGALS", "BILLS", "BRONCOS", "BROWNS", "BUCCANEERS", "CARDINALS",
    "CHARGERS", "CHIEFS", "COLTS", "COMMANDERS", "COWBOYS", "DOLPHINS", "EAGLES", "FALCONS",
    "GIANTS", "JAGUARS", "JETS", "LIONS", "PACKERS", "PANTHERS", "PATRIOTS", "RAIDERS",
    "RAMS", "RAVENS", "SAINTS", "SEAHAWKS", "STEELERS", "TEXANS", "TITANS", "VIKINGS"})

TYPES = ("SIGNED", "RELEASED", "TRADED", "RE_SIGNED", "EXTENDED", "RESTRUCTURED", "OTHER")

# Vision sometimes returns Cyrillic/Greek look-alikes inside Latin words (madden_capture.HOMOGLYPHS)
HOMOGLYPHS = str.maketrans("АВСЕНІЈКМОРЅТХУаеорсухіјΑΒΕΗΙΚΜΝΟΡΤΧΥΖ",
                           "ABCEHIJKMOPSTXYaeopcyxijABEHIKMNOPTXYZ")
OCR_DIGITS = str.maketrans("TILO", "1110")

# Heading phases in season order. Offseason rows are assumed to carry the year of the season that just ended
# and to come after its postseason (console fact 5, unverified).
PHASES = {"PRESEASON": 0, "SEASON": 1, "POST SEASON": 2, "OFFSEASON": 3}
HEADING_RE = re.compile(r"^([0-9TILO]{4}) ?(PRE ?SEASON|POST ?SEASON|OFF ?SEASON|SEASON)\s*[-–—:]?\s*(.*)$")
WEEK_RE = re.compile(r"^WEEK ?([0-9TILO]{1,2})$")
POST_ORDER = ["WILD CARD", "DIVISIONAL", "CONFERENCE", "PRO BOWL", "SUPER BOWL"]


def fold(s):
    """Upper case, look-alikes folded, whitespace collapsed, spaces inside brackets dropped."""
    s = re.sub(r"\s+", " ", (s or "").translate(HOMOGLYPHS).upper()).strip()
    s = re.sub(r"\(\s+", "(", s)
    return re.sub(r"\s+\)", ")", s)


def clean_heading(s):
    """'2036 SEASON - WEEKT' -> '2036 SEASON - WEEK 1', '2O36 SEASON-WEEK 1T' -> '2036 SEASON - WEEK 11',
    '2036 POST SEASON - WILDCARD' -> '2036 POST SEASON - WILD CARD'. Unreadable text comes back folded."""
    t = fold(s)
    m = HEADING_RE.match(t)
    if not m:
        return t
    year = m.group(1).translate(OCR_DIGITS)
    phase = {"PRESEASON": "PRESEASON", "PRE SEASON": "PRESEASON", "POSTSEASON": "POST SEASON",
             "POST SEASON": "POST SEASON", "OFFSEASON": "OFFSEASON", "OFF SEASON": "OFFSEASON"}.get(m.group(2), m.group(2))
    rest = m.group(3).strip(" -")
    w = WEEK_RE.match(rest)
    if w:
        rest = f"WEEK {int(w.group(1).translate(OCR_DIGITS))}"
    else:
        rest = rest.replace("WILDCARD", "WILD CARD").replace("SUPERBOWL", "SUPER BOWL").replace("PROBOWL", "PRO BOWL")
    return f"{year} {phase} - {rest}" if rest else f"{year} {phase}"


def _parts(h):
    m = re.match(r"^(\d{4}) (PRESEASON|SEASON|POST SEASON|OFFSEASON)(?: - (.*))?$", clean_heading(h))
    return (int(m.group(1)), m.group(2), m.group(3) or "") if m else None


def heading_year(h):
    p = _parts(h)
    return p[0] if p else None


def heading_ordinal(h):
    """(year, phase rank, week or stage) for ordering headings; None when the heading is unreadable.
    Preseason < regular season < postseason < offseason within a year."""
    p = _parts(h)
    if not p:
        return None
    year, phase, rest = p
    w = WEEK_RE.match(rest)
    if w:
        sub = int(w.group(1))
    elif phase == "POST SEASON":
        sub = next((i for i, r in enumerate(POST_ORDER) if rest.startswith(r)), len(POST_ORDER))
    else:
        sub = 0                                   # offseason stages: order unknown (console fact 5)
    return (year, PHASES[phase], sub)


def heading_week(h):
    """games.week for a heading: 'SEASON - WEEK 4' -> 4, 'POST SEASON - WILD CARD' -> 19 ... 'SUPER BOWL' -> 23;
    preseason and offseason headings give None."""
    p = _parts(h)
    if not p:
        return None
    _, phase, rest = p
    if phase == "SEASON":
        return db_week(f"SEASON {rest}")
    if phase == "POST SEASON":
        return db_week(f"POST SEASON {rest}")
    return None


# ------------------------------------------------------------------ rows ----
POS = r"\((?P<pos>[A-Z]{1,5})\)"
TEAM = r"(?P<team>[0-9A-Z]+)"
# "TO 1 YEAR, $3.93M CONTRACT" (signing) and "TO A 4-YEAR, $120M EXTENSION" (re-sign, JR 2026-10-19)
TERMS = r"TO (?:AN? )?(?P<years>[0-9TILO]{1,2}) ?-? ?YEARS?[,.]? ?(?P<amount>[$S]? ?[\d.,]+ ?[MK])(?: CONTRACT| EXTENSION)?"
# A roster move's verb. A row without one (draft-class notice and the like) isn't a move: it is skipped, not
# stored (JR, 2026-10-19). A row with one that the grammar can't read is kept for review.
MOVE_RE = re.compile(r"\b(?:RE ?- ?SIGN|SIGN|RELEASE|TRADE|ACQUIRE|EXTEN|WAIVE|CLAIM|RESTRUCTURE)")
# Rows seen on the console (2026-10-19) that are not roster moves, though they name a player: commissioner edits
# ("(QB) GEORGE CRAWFORD WAS EDITED BY …"), draft-class access, position changes ("PANTHERS MOVE LT … TO (LG)";
# the next roster scan has the new position).
NOT_MOVE_RE = re.compile(r"\b(?:WAS EDITED BY|HAS BEEN ACCESSED BY|HAS BEEN EDITED)\b|^\S+ MOVE ")
# "RAMS SIGN (LT) J.C. FRIEDMAN TO THEIR PRACTICE SQUAD" (seen 2026-10-19): a practice-squad signing, no terms
PS = r"(?P<ps> (?:TO|FROM) (?:THEIR|THE) PRACTICE SQUAD)"
SIGN_RE = re.compile(rf"^{TEAM} SIGN(?:ED|S)? (?:(?:A )?FREE AGENT |FA |FROM OTHER TEAM PRACTICE SQUAD )?{POS} (?P<name>.+?)(?: {TERMS}|{PS})?$")
RELEASE_RE = re.compile(rf"^{TEAM} RELEASE[DS]? {POS} (?P<name>.+?){PS}?$")
RESIGN_RE = re.compile(rf"^{TEAM} RE ?- ?SIGN(?:ED|S)? {POS} (?P<name>.+?)(?: {TERMS})?$")
# "BEARS HAVE RESTRUCTURED THE CONTRACT OF (QB) TY SIMPSON GENERATING $11.3M IN ADDITIONAL CAP SPACE"
RESTRUCTURE_RE = re.compile(rf"^{TEAM} (?:HAVE |HAS )?RESTRUCTURED THE CONTRACT OF {POS} (?P<name>.+?) GENERATING "
                            r"(?P<amount>[$S]? ?[\d.,]+ ?[MK]) IN ADDITIONAL CAP SPACE$")
EXTEND_RE = re.compile(rf"^{TEAM} .*?{POS} (?P<name>.+?)(?= TO |,|$)")    # any row that says EXTEN...
LEG_RE = re.compile(r"\((?P<pos>[A-Z]{1,5})\) (?P<name>.+?)(?= TO | FROM | FOR | AND |, |;| \(|$)")
PICK_RE = re.compile(r"\b(?P<pick>[0-9TILO]{4} (?:\w+ )?(?:ROUND|RD)(?: [0-9TILO]{1,2})?(?: \([0-9TILO]+\))? (?:DRAFT )?PICK)S?\b")


def norm_amount(a):
    """'$3.93M', 'S3.93 M', '$850K' -> '3.93M', '850K'."""
    return re.sub(r"[\s,]", "", (a or "").upper()).lstrip("$S") or None


def _years(y):
    return int(y.translate(OCR_DIGITS)) if y else None


def _empty(text, team=None, kind="OTHER"):
    return {"type": kind, "team": team, "to_team": None, "pos": None, "name": None, "years": None,
            "amount_text": None, "legs": [], "assets": [], "ok": False, "flags": [], "text": text}


def parse_row(text, teams=TEAM_NICKNAMES):
    """One row's text -> {type, team, to_team, pos, name, years, amount_text, legs, assets, ok, flags, text}.

    type is one of TYPES. ok is False when the grammar couldn't place the row (it's then OTHER, or TRADED for a
    row that says TRADE); flags list why (unknown_team, no_terms, unparsed, trade_*). Never raises."""
    t = fold(text)
    first = t.split(" ", 1)[0] if t else ""
    if re.search(r"\bTRADE|\bACQUIRE", t):
        return _parse_trade(t, teams)
    m = RESTRUCTURE_RE.match(t)
    if m:
        d = _empty(t, m.group("team"), "RESTRUCTURED")
        d.update(pos=m.group("pos"), name=m.group("name").strip(" ,."), amount_text=norm_amount(m.group("amount")),
                 ok=m.group("team") in teams)
        if not d["ok"]:
            d["flags"].append("unknown_team")
        return d
    rules = [("RE_SIGNED", RESIGN_RE), ("SIGNED", SIGN_RE), ("RELEASED", RELEASE_RE)]
    if re.search(r"\bEXTEN(?:D|DED|DS|SION)\b", t) and not re.search(r"\bRE ?- ?SIGN", t):
        rules = [("EXTENDED", EXTEND_RE)]          # "RE-SIGN … EXTENSION" is a re-signing (JR's wording)
    for kind, rx in rules:
        m = rx.match(t)
        if not m:
            continue
        d = _empty(t, m.group("team"), kind)
        d.update(pos=m.group("pos"), name=m.group("name").strip(" ,."), ok=True)
        terms = m if "years" in rx.groupindex else re.search(TERMS, t)
        if terms and terms.group("years"):
            d["years"], d["amount_text"] = _years(terms.group("years")), norm_amount(terms.group("amount"))
        if "ps" in rx.groupindex and m.group("ps"):
            d["flags"].append("practice_squad")       # no contract terms on the row
        elif kind == "SIGNED" and not (d["years"] and d["amount_text"]):
            d["flags"].append("no_terms")
        if d["team"] not in teams:
            d["flags"].append("unknown_team")
            d["ok"] = False
        if kind == "SIGNED":
            d["to_team"] = d["team"]
        return d
    d = _empty(t, first if first in teams else None)
    d["flags"].append("unparsed")
    return d


def is_move(text):
    """Does the row name a roster move: a move verb, or a '(POS) NAME' player group (so a verb OCR mangled still
    counts)? Rows that don't are skipped by extraction's re-read and by the loader."""
    t = fold(text)
    if NOT_MOVE_RE.search(t):
        return False
    return bool(MOVE_RE.search(t) or re.search(r"\([A-Z]{1,5}\) [A-Z]", t))


def _parse_trade(t, teams):
    """Tolerant trade grammar (wording unverified, console fact 7): the first nickname is the team giving the
    players named before 'FOR', 'TO <TEAM>' (or 'FROM <TEAM>' after ACQUIRE) is the other side, every
    '(POS) NAME' is a player leg and every 'YYYY <N> ROUND PICK' or 'YYYY ROUND N (overall) DRAFT PICK' (Madden's
    wording, C8) an asset leg. Legs after 'FOR' go the other
    way. Anything it can't place is flagged; one row can hold several legs."""
    d = _empty(t, kind="TRADED")
    named = [w for w in re.findall(r"[0-9A-Z]+", t) if w in teams]
    if not named:
        d["flags"].append("unknown_team")
        return d
    a = named[0]
    m_to = re.search(r"\bTO (?:THE )?([0-9A-Z]+)", t)
    m_from = re.search(r"\bFROM (?:THE )?([0-9A-Z]+)", t)
    acquire = re.search(r"\bACQUIRE", t) and m_from and m_from.group(1) in teams
    if acquire:
        giver, receiver = m_from.group(1), a
    else:
        b = m_to.group(1) if m_to and m_to.group(1) in teams else next((n for n in named[1:] if n != a), None)
        giver, receiver = a, b
    d["team"], d["to_team"] = giver, receiver
    if not receiver or receiver == giver:
        d["flags"].append("trade_no_partner")
    head, _, back = t.partition(" FOR ")
    for part, (src, dst) in ((head, (giver, receiver)), (back, (receiver, giver))):
        for m in LEG_RE.finditer(part):
            d["legs"].append({"pos": m.group("pos"), "name": m.group("name").strip(" ,."), "from": src, "to": dst})
        for m in PICK_RE.finditer(part):
            pick = m.group("pick")
            pick = pick[:4].translate(OCR_DIGITS) + pick[4:]
            d["assets"].append({"asset": pick, "from": src, "to": dst})
    if not d["legs"] and not d["assets"]:
        d["flags"].append("trade_no_legs")
    if d["legs"]:
        d["pos"], d["name"] = d["legs"][0]["pos"], d["legs"][0]["name"]
    d["ok"] = not d["flags"]
    return d


def legs(parsed):
    """The transactions rows one list row becomes: [{suffix, type, from, to, pos, name, years, amount_text,
    asset}]. A trade row gives one leg per player and per pick; every other row gives one."""
    kind = parsed["type"]
    base = {"type": kind, "pos": parsed["pos"], "name": parsed["name"], "years": parsed["years"],
            "amount_text": parsed["amount_text"], "asset": None, "suffix": ""}
    if kind != "TRADED":
        frm, to = {"SIGNED": (None, parsed["team"]), "RELEASED": (parsed["team"], None),
                   "RE_SIGNED": (None, parsed["team"]), "EXTENDED": (None, parsed["team"]),
                   "RESTRUCTURED": (None, parsed["team"])}.get(kind, (None, None))
        return [dict(base, **{"from": frm, "to": to})]
    out = []
    for i, lg in enumerate(parsed["legs"]):
        out.append(dict(base, pos=lg["pos"], name=lg["name"], suffix=f"#P{i + 1}", **{"from": lg["from"], "to": lg["to"]}))
    for i, a in enumerate(parsed["assets"]):
        out.append(dict(base, pos=None, name=None, asset=a["asset"], suffix=f"#A{i + 1}",
                        **{"from": a["from"], "to": a["to"]}))
    return out or [dict(base, **{"from": parsed["team"], "to": parsed["to_team"]})]


def row_key(parsed, text=None):
    """Normalized key for the unique index. A parsed row's key is built from its fields, so two OCR readings
    that differ by a stray character or space still collide; an unparsed row's key is its folded text."""
    if not parsed.get("ok"):
        raw = re.sub(r"[^A-Z0-9$.,() -]", "", fold(text if text is not None else parsed.get("text")))
        return "raw:" + re.sub(r"\s+", " ", raw).strip()
    if parsed["type"] == "TRADED":
        lg = sorted(f"{x['pos']}:{full_key(x['name'])}:{x['from']}>{x['to']}" for x in parsed["legs"])
        lg += sorted(f"{re.sub(r'[^A-Z0-9]', '', x['asset'])}:{x['from']}>{x['to']}" for x in parsed["assets"])
        return "|".join(["TRADED", parsed["team"] or "", parsed["to_team"] or "", *lg])
    return "|".join([parsed["type"], parsed["team"] or "", parsed["to_team"] or "", parsed["pos"] or "",
                     full_key(parsed["name"]), str(parsed["years"] or ""), parsed["amount_text"] or ""])


def assign_seq(rows):
    """1-based seq per (heading, row_key), counted from the OLDEST row of the heading group upward. rows are
    newest first (screen order) with 'heading' and 'row_key'. New rows are prepended at the top, so the older
    rows keep their seq when the list grows. Returns the seqs in the same order."""
    seen, out = {}, [0] * len(rows)
    for i in range(len(rows) - 1, -1, -1):
        k = (rows[i]["heading"], rows[i]["row_key"])
        seen[k] = seen.get(k, 0) + 1
        out[i] = seen[k]
    return out


def fix_heading_order(rows):
    """rows newest first, each with 'heading' (cleaned) and 'flags' (list). Headings must not increase down the
    list. A row whose heading is unreadable or out of order while its two neighbours agree takes their heading
    (flag heading_fixed); any other break is flagged heading_order for a re-read. Returns rows (updated)."""
    ords = [heading_ordinal(r["heading"]) for r in rows]
    n = len(rows)
    for i, r in enumerate(rows):
        prev = ords[i - 1] if i else None
        nxt = ords[i + 1] if i + 1 < n else None
        cur = ords[i]
        bad = cur is None or (prev is not None and cur > prev) or (nxt is not None and cur < nxt)
        if not bad:
            continue
        if prev is not None and prev == nxt:
            src = rows[i - 1]
        elif cur is None and (i == 0 or i == n - 1) and (prev or nxt):
            src = rows[i - 1] if prev is not None else rows[i + 1]      # the list's first or last row
        else:
            continue
        r["heading"], ords[i] = src["heading"], heading_ordinal(src["heading"])
        r["flags"].append("heading_fixed")
    for i, r in enumerate(rows):                  # what's left out of order goes back for a re-read
        if ords[i] is None or (i and ords[i - 1] is not None and ords[i] > ords[i - 1]):
            r["flags"].append("heading_order")
    return rows


def needs_reread(parsed, heading_flags=()):
    """Should this row go to Gemini (plan §2.4)? Grammar failure, heading out of order, missing length or
    amount on a signing, or a team that isn't one of the 32."""
    if not is_move(parsed["text"]) or parsed["type"] == "TRADED":
        return False                                # skipped by the loader (non-moves, trades): no Gemini call
    return (not parsed["ok"] or "heading_order" in heading_flags or "no_terms" in parsed["flags"]
            or "unknown_team" in parsed["flags"])
