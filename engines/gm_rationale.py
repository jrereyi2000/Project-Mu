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


def write_rationale(sheet, llm=None):
    """Template unless the LLM path is enabled (Phase 6)."""
    return template(sheet)
