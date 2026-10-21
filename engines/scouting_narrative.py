"""
Scouting prose behind the Gemini seam (docs/team_management/04_SCOUTING_AND_DRAFT_PLAN.md §2.7; PROJECT_SPEC §2.18).

Numbers are always deterministic (stat lines, measurables, grades, picks); the LLM only rewrites prose:
  1. up to MAX_NOTES weekly scouting notes for the biggest movers (llm_usage label `scouting_notes`);
  2. a 2-3 sentence rationale for each Round 1 pick in the GM's voice (label `draft_rationale`).
Each text has a fact sheet; validate_scout_text rejects any number, school, NFL team or height that isn't on it, an
injury described as ongoing, any mention of OVR / ratings / Madden, and the hallucination policy's banned claims.
On a rejection, an error or no key, the template stays (text_source TEMPLATE). A stored text is never regenerated.
The llm is engines.gm_llm.make_llm(label) or None; tests pass a fake callable.
"""
import json
import re

MAX_NOTES = 8
NUM_RE = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w])")
HEIGHT_RE = re.compile(r"\b([4-7])\s*(?:-|'|’|ft\.?\s*)\s*(\d{1,2})\b")
ONGOING_RE = re.compile(r"\b(out for|will miss|miss(?:es|ing)? (?:the|time|games?)|season[- ]ending|surgery|torn|"
                        r"ongoing|still recovering|sidelined|not (?:yet )?cleared|limited at the draft)\b", re.I)
RATING_RE = re.compile(r"\b(OVR|overall rating|true value|rated \d+|rating of|madden|true rank|front[- ]office quality)\b",
                       re.I)
BANNED_RE = re.compile(r"\b(arrest\w*|charged|charges|suspen\w+|police|freshman|dismissed)\b", re.I)


# ------------------------------------------------------------------ sheets ----
def _numbers(v, out):
    if isinstance(v, bool) or v is None:
        return
    if isinstance(v, (int, float)):
        out.add(float(v))
    elif isinstance(v, str):
        for n in NUM_RE.findall(v):
            out.add(float(n))
    elif isinstance(v, dict):
        for x in v.values():
            _numbers(x, out)
    elif isinstance(v, (list, tuple)):
        for x in v:
            _numbers(x, out)


def fact_sheet(event, prospect, team=None, extra=None):
    """The facts one event's prose may use. prospect: {full_name, position, school, class_year, height_in, weight,
    age}; team: (abbr, name) for a VISIT or a pick."""
    from engines.scouting_value import POSITION_WORDS
    sch = prospect.get("school") or {}
    payload = {k: v for k, v in (event.get("payload") or {}).items() if not str(k).startswith("_")}
    return {"kind": event.get("kind"), "name": prospect["full_name"].title(),
            "position": POSITION_WORDS.get((prospect.get("position") or "").upper(), "prospect"),
            "school": sch.get("display") or (prospect.get("college") or "").title(), "nickname": sch.get("nickname"),
            "conference": sch.get("conference"), "class_year": prospect.get("class_year"),
            "height_in": prospect.get("height_in"), "weight": prospect.get("weight"), "age": prospect.get("age"),
            "payload": payload, "template": event.get("blurb"), "team": team, **(extra or {})}


def sheet_schools(sheet):
    names = {sheet.get("school"), (sheet.get("payload") or {}).get("opponent")}
    for k in ("schools",):
        names |= set(sheet.get(k) or ())
    return {n.upper() for n in names if n}


# --------------------------------------------------------------- validator ----
def validate_scout_text(text, sheet, league=None):
    """(ok, why). league: {"schools": {DISPLAY NAME}, "teams": {NFL NICKNAME / CITY / ABBR}} upper case (defaults:
    every colleges.json school, no NFL teams)."""
    if not text or not text.strip():
        return False, "empty"
    if RATING_RE.search(text):
        return False, f"mentions '{RATING_RE.search(text).group(0)}'"
    if BANNED_RE.search(text):
        return False, f"banned claim '{BANNED_RE.search(text).group(0)}'"
    if ONGOING_RE.search(text):
        return False, f"injury described as ongoing ('{ONGOING_RE.search(text).group(0)}')"
    for m in HEIGHT_RE.finditer(text):
        inches = int(m.group(1)) * 12 + int(m.group(2))
        if sheet.get("height_in") != inches:
            return False, f"height {m.group(0)} isn't {sheet.get('height_in')} inches"
    allowed = set()
    _numbers(sheet, allowed)
    hi = sheet.get("height_in")
    if hi:
        allowed |= {float(hi // 12), float(hi % 12)}
    plain = HEIGHT_RE.sub(" ", text)
    for n in NUM_RE.findall(plain):
        if float(n) not in allowed:
            return False, f"number {n} not on the sheet"
    if league is None:
        from engines.scouting_value import colleges
        league = {"schools": {s["display"].upper() for s in colleges()["schools"].values()}, "teams": set()}
    up = " " + re.sub(r"[^A-Z0-9&() ]", " ", text.upper()) + " "
    mine = sheet_schools(sheet)
    for m in mine:                                     # the sheet's schools first, so "Texas" inside "Texas Tech" passes
        up = up.replace(f" {m} ", " ")
    for school in sorted(league.get("schools", ()), key=len, reverse=True):
        if school in mine or any(school in m for m in mine):
            continue
        if f" {school} " in up:
            return False, f"school {school.title()} isn't on the sheet"
    from engines.gm_rationale import names_team
    team = sheet.get("team")
    mine_t = {str(x).upper() for x in (team or ()) if x}
    full_t = " ".join(mine_t)
    for t in league.get("teams", ()):
        if not t or t in mine_t or t in full_t:
            continue
        if names_team(text, up, t):
            return False, f"team {t.title()} isn't on the sheet"
    return True, None


# ----------------------------------------------------------------- writers ----
def note_prompt(sheet):
    return ("You are a college football scout writing a short weekly note for an NFL draft board. Rewrite the "
            "template below as 2-3 vivid sentences. Use only the facts in the JSON: no other numbers, schools, NFL "
            "teams or heights. Never mention ratings, OVR or Madden. An injury is day-to-day at most. You may add "
            "colour (tape study, body language, scheme details) without new facts. Answer with the note only.\n"
            f"FACTS: {json.dumps(sheet, default=str)}\nTEMPLATE: {sheet.get('template')}")


def write_text(sheet, prompt, llm, league=None):
    """(text, source, rejected_reason). The LLM's text when it validates, else the sheet's template."""
    from engines.gm_llm import call
    got = call(llm, prompt) if llm is not None else None
    if got:
        got = got.strip().strip('"')
        ok, why = validate_scout_text(got, sheet, league)
        if ok:
            return got, "LLM", None
        return sheet.get("template"), "TEMPLATE", why
    return sheet.get("template"), "TEMPLATE", None


def movers(out, limit=MAX_NOTES, within=150):
    """The step's biggest movers that have an event: [key], largest |move| first."""
    cons = out["cons"]
    keys = [k for k, r in cons.items() if out["events"].get(k) and r["rank"] <= within and abs(r["move"]) > 0]
    keys.sort(key=lambda k: (-abs(cons[k]["move"]), cons[k]["rank"]))
    return keys[:limit]


def write_notes(out, llm, league=None, log=print):
    """Rewrite the top event blurb of up to MAX_NOTES movers through the llm (in place; text_source on the event).
    No llm: nothing changes. Returns the number written by the LLM."""
    if llm is None:
        return 0
    by_key = {p["key"]: p for p in out["cls"]}
    n = 0
    for k in movers(out):
        ev = next((e for e in out["events"][k] if e["kind"] != "VISIT"), None)
        if not ev:
            continue
        sheet = fact_sheet(ev, by_key[k])
        text, src, why = write_text(sheet, note_prompt(sheet), llm, league)
        if src == "LLM":
            ev["blurb"], ev["text_source"] = text, "LLM"
            n += 1
        elif why:
            log(f"  scouting note for {sheet['name']} rejected: {why}")
    return n


# ---------------------------------------------------------- draft rationale ----
def rationale_template(sheet):
    need = sheet.get("need_group")
    why = (f"fills a need at {need}" if need else "was the best player left on our board")
    return (f"{sheet['gm']}: {sheet['name']} {why}. The {sheet['school']} {sheet['position']} is a "
            f"{sheet['grade_label']} talent on our board, and we weren't going to let him get past us.")


def rationale_prompt(sheet):
    return ("You are an NFL general manager explaining a first-round pick in 2-3 sentences, in this voice: "
            f"{sheet.get('voice') or 'measured, confident'}. Use only the facts in the JSON: no numbers, schools, "
            "teams or heights that aren't there; never mention ratings, OVR or Madden. Answer with the rationale only.\n"
            f"FACTS: {json.dumps(sheet, default=str)}\nTEMPLATE: {rationale_template(sheet)}")


def draft_rationale(sheet, llm=None, league=None):
    """{text, source, rejected} for one Round 1 pick (label draft_rationale through gm_llm)."""
    sheet = dict(sheet, template=rationale_template(sheet))
    text, src, why = write_text(sheet, rationale_prompt(sheet), llm, league)
    return {"text": text, "source": src, "rejected": why}
