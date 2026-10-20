"""
Trade rationale writer (docs/team_management/03_GM_AGENTS_PLAN.md §2.8; PROJECT_SPEC §2.17).

build_fact_sheet() is everything a rationale may state: the assets (position, name, OVR, age, dev, cap hit,
years left), both teams' record, direction, needs, cap before and after, the GM and the head coach. template()
writes the headline and each side's two sentences from the sheet alone. write_rationale() asks the LLM (the
engines.gm_llm seam) for the same in each GM's voice and keeps it only when validate_rationale() finds no number,
dollar amount, player or team that isn't on the sheet; otherwise, or without a key, the template. The LLM never
chooses or changes a deal.
"""
import json
import re

from engines.gm_direction import SLOTS

GROUP_NAMES = {"QB": "quarterback", "RB": "running back", "WR": "receiver", "TE": "tight end", "OT": "tackle",
               "IOL": "interior line", "EDGE": "edge rusher", "DT": "defensive tackle", "LB": "linebacker",
               "CB": "cornerback", "S": "safety", "K": "kicker", "P": "punter"}


def money(v):
    if v is None:
        return "unknown"
    return f"${v / 1e6:.2f}M" if abs(v) >= 1e6 else f"${v / 1e3:.0f}K"


def build_fact_sheet(b, deal):
    """The deal's facts as a plain dict (JSON-safe)."""
    from engines.gm_trade_engine import abbr, cap_effects, cap_hit_now, pick_label, team_name
    season = b["season"]
    caps = cap_effects(deal, b)
    teams = {}
    for side, t in (("a", deal["a"]), ("b", deal["b"])):
        gm = b["gm"].get(t, {})
        ctx = b["ctx"].get(t, {})
        rec = b.get("records", {}).get(t)
        teams[side] = {"team_id": t, "abbr": abbr(b, t), "team": team_name(b, t),
                       "nickname": b["teams"].get(t, {}).get("name"), "record": rec,
                       "direction": ctx.get("direction"),
                       "needs": [GROUP_NAMES.get(n["group"], n["group"]) for n in (ctx.get("needs") or [])[:3]],
                       "cap_before": money(caps[t]["before"]), "cap_after": money(caps[t]["after"]),
                       "gm": gm.get("gm_name"), "voice": gm.get("voice"),
                       "coach": b.get("coaches", {}).get(t),
                       "urgent": (ctx.get("urgent_need") or {}).get("group")}
    assets = []
    for x in deal["assets"]:
        if x["type"] == "PLAYER":
            p = b["players"][x["player_id"]]
            c = p.get("contract") or {}
            assets.append({"kind": "player", "name": p.get("name"), "pos": p.get("pos"), "group": p.get("group"),
                           "ovr": p.get("ovr"), "age": p.get("age"), "dev": p.get("dev") or "Normal",
                           "cap_hit": money(cap_hit_now(p, season)),
                           "years_left": (c.get("end_season", season) - season + 1),
                           "from": abbr(b, x["from"]), "to": abbr(b, x["to"])})
        else:
            assets.append({"kind": "pick", "name": pick_label(b, x["pick"]), "from": abbr(b, x["from"]),
                           "to": abbr(b, x["to"])})
    return {"season": season, "week": b["week"], "window": b["window"], "trigger": deal.get("trigger"),
            "teams": teams, "assets": assets}


def _gets(sheet, side):
    me = sheet["teams"][side]["abbr"]
    return [a for a in sheet["assets"] if a["to"] == me]


def _describe(a):
    if a["kind"] == "pick":
        return f"the {a['name']} pick"
    return f"{a['pos']} {a['name']} ({a['ovr']} OVR)"


def template(sheet):
    """{headline, rationale_a, rationale_b, source: TEMPLATE} from the sheet alone."""
    A, B = sheet["teams"]["a"], sheet["teams"]["b"]
    ga, gb = _gets(sheet, "a"), _gets(sheet, "b")
    star_a = next((a for a in ga if a["kind"] == "player"), ga[0] if ga else None)
    star_b = next((a for a in gb if a["kind"] == "player"), gb[0] if gb else None)
    head = (f"{A['nickname']} acquire {star_a['name']} from the {B['nickname']}" if star_a and star_a["kind"] == "player"
            else f"{A['nickname']} and {B['nickname']} swap {len(sheet['assets'])} assets")
    need = GROUP_NAMES.get(star_a.get("group")) if star_a and star_a["kind"] == "player" else (
        A["needs"][0] if A["needs"] else None)
    why_a = {"INJURY": f"With an injury at {GROUP_NAMES.get(A['urgent'], 'a key spot')}, we needed an answer now",
             "SELL": f"We're in a {(A['direction'] or 'retool').lower()} year and this return fits our timeline",
             }.get(sheet["trigger"], f"We came into the week needing help at {need}" if need else "This makes us better")
    ra = (f"{A['gm']}: {why_a}. {_describe(star_a) if star_a else 'This return'} fits what the {A['nickname']} "
          f"are building, and our cap goes from {A['cap_before']} to {A['cap_after']}.")
    rb = (f"{B['gm']}: We like what {', '.join(_describe(a) for a in gb) or 'this'} brings the {B['nickname']}. "
          f"It suits a {(B['direction'] or 'retool').lower()} plan, with our cap at {B['cap_after']} after the deal.")
    return {"headline": head, "rationale_a": ra, "rationale_b": rb, "source": "TEMPLATE"}


# ------------------------------------------------------------------ LLM ----
def prompt(sheet):
    """The rationale prompt: the fact sheet, each GM's voice, the hallucination rule, JSON out."""
    A, B = sheet["teams"]["a"], sheet["teams"]["b"]
    return (
        "You write for a simulated NFL league's front-office wire. Two general managers have agreed a trade in "
        "principle. Write a headline and each GM's own 2-3 sentence explanation, in his voice.\n"
        f"GM A: {A['gm']} of the {A['team']} (voice: {A['voice'] or 'plain'}).\n"
        f"GM B: {B['gm']} of the {B['team']} (voice: {B['voice'] or 'plain'}).\n"
        "FACT SHEET (JSON; the only facts you may state):\n"
        f"{json.dumps(sheet, indent=1, default=str)}\n"
        "Rules: invent untracked colour freely (front-office chatter, how long he has liked a player, what the "
        "building feels like). Never state a number, dollar amount, player, team, record or contract fact that "
        "isn't on the fact sheet. Don't call the trade final or completed: it still has to go through.\n"
        'Answer with JSON only: {"headline": "...", "rationale_a": "...", "rationale_b": "..."}')


MONEY_RE = re.compile(r"\$\s?(\d+(?:\.\d+)?)\s?([MK])?", re.I)
NUM_RE = re.compile(r"(?<![\w.$])(\d+(?:\.\d+)?)(?![\w.]*\d)")


def _sheet_numbers(sheet):
    """(ints/floats allowed as plain numbers, money values in dollars) from the sheet."""
    nums, money_vals = set(), set()

    def walk(v):
        if isinstance(v, bool) or v is None:
            return
        if isinstance(v, (int, float)):
            nums.add(float(v))
        elif isinstance(v, str):
            for m in MONEY_RE.finditer(v):
                money_vals.add(_dollars(m))
            for n in re.findall(r"\d+(?:\.\d+)?", v):
                nums.add(float(n))
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
    walk(sheet)
    return nums, money_vals


def _dollars(m):
    v = float(m.group(1))
    unit = (m.group(2) or "").upper()
    return v * (1e6 if unit == "M" else 1e3 if unit == "K" else 1)


def validate_rationale(text, sheet, league=None):
    """(ok, why). Rejects a dollar amount or number that isn't on the sheet, and a league player or team that
    isn't in the deal (league: {"players": {NAME}, "teams": {NICKNAME / CITY / ABBR}}, upper case). Invented colour
    without facts passes."""
    if not text or not text.strip():
        return False, "empty"
    nums, money_vals = _sheet_numbers(sheet)
    for m in MONEY_RE.finditer(text):
        d = _dollars(m)
        if not any(abs(d - x) <= 0.051e6 for x in money_vals):
            return False, f"amount {m.group(0)} not on the sheet"
    plain = MONEY_RE.sub(" ", text)
    for n in NUM_RE.findall(plain):
        if float(n) not in nums:
            return False, f"number {n} not on the sheet"
    up = " " + re.sub(r"[^A-Z0-9' ]", " ", text.upper()) + " "
    league = league or {}
    on_sheet_players = {(a.get("name") or "").upper() for a in sheet["assets"] if a["kind"] == "player"}
    for name in league.get("players", ()):
        if name and name not in on_sheet_players and f" {name} " in up:
            return False, f"player {name.title()} isn't in the deal"
    mine = set()
    for t in sheet["teams"].values():
        mine |= {str(t.get("abbr") or "").upper(), str(t.get("nickname") or "").upper(),
                 str(t.get("team") or "").upper()}
        mine |= set(str(t.get("team") or "").upper().split())
    for team in league.get("teams", ()):
        if team and team not in mine and f" {team} " in up:
            return False, f"team {team.title()} isn't in the deal"
    return True, None


def parse_answer(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        d = json.loads(m.group(0) if m else text)
    except (ValueError, TypeError, AttributeError):
        return None
    if not all(isinstance(d.get(k), str) and d.get(k).strip() for k in ("headline", "rationale_a", "rationale_b")):
        return None
    return {k: d[k].strip() for k in ("headline", "rationale_a", "rationale_b")}


def write_rationale(sheet, llm=None, league=None):
    """The LLM's headline and rationale when it answers and every field validates; else the template. The
    result's source says which (rationale_source)."""
    from engines.gm_llm import call
    got = parse_answer(call(llm, prompt(sheet))) if llm is not None else None
    if got and all(validate_rationale(got[k], sheet, league)[0] for k in got):
        return dict(got, source="LLM")
    out = template(sheet)
    if got:
        out["rejected"] = [validate_rationale(got[k], sheet, league)[1] for k in got
                           if not validate_rationale(got[k], sheet, league)[0]]
    return out


def write_rationales(sheets, llm=None, league=None):
    """write_rationale for several sheets (the LLM's calls are metered by the gm_llm client; flush it after)."""
    return [write_rationale(s, llm, league) for s in sheets]


def league_names(b):
    """The validator's league: every rostered player's name and every team's nickname, city and abbreviation."""
    players = {(p.get("name") or "").upper() for p in b["players"].values() if p.get("name")}
    teams = set()
    for t in b["teams"].values():
        teams |= {str(t.get("abbr") or "").upper(), str(t.get("name") or "").upper(), str(t.get("city") or "").upper()}
    return {"players": players - {""}, "teams": teams - {""}}
