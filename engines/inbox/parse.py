"""
Typed text -> a draft JR confirms (FRONT_OFFICE_INBOX_PLAN.md §2.3, D10).

Rules parse first; the model is the fallback, limited to listed ids and numeric ranges. A draft acts on nothing: the
inbox shows it ("Did you mean: Offer 3 years, $40.0M total, $15.0M guaranteed, $0 bonus?") and only JR's confirm
click sends it.

  parse_contract_terms(text) -> {"years", "total", "guaranteed", "bonus"} | None     whole dollars
  parse_with_llm(llm, text, schema, choices, budget_s) -> dict | None
  draft_summary(option_id, payload) -> str
"""
import json
import re

MAX_YEARS = 7
MAX_CONTRACT_TOTAL = 1_000_000_000

_YEARS = re.compile(r"\b(\d{1,2})\s*-?\s*(?:years?|yrs?|y)\b", re.I)
_WORD_YEARS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}
_WORD_YEARS_RE = re.compile(r"\b(" + "|".join(_WORD_YEARS) + r")\s*-?\s*(?:years?|yrs?)\b", re.I)
_AMOUNT = re.compile(r"\$?\s*(\d+(?:,\d{3})*(?:\.\d+)?)\s*(million|mil|mm|m|thousand|k)?\b", re.I)
_GUARANTEED = re.compile(r"^\s*(?:fully\s+)?(?:guaranteed|guarantee|guar|gtd|gteed)\b", re.I)
_BONUS = re.compile(r"^\s*(?:signing\s+)?bonus\b", re.I)
_BONUS_BEFORE = re.compile(r"(?:signing\s+)?bonus\s*(?:of|:)?\s*$", re.I)
_GUARANTEED_BEFORE = re.compile(r"(?:guaranteed|gtd)\s*(?:of|:)?\s*$", re.I)


def _dollars(number, unit):
    v = float(number.replace(",", ""))
    unit = (unit or "").lower()
    if unit in ("million", "mil", "mm", "m"):
        return int(round(v * 1_000_000))
    if unit in ("thousand", "k"):
        return int(round(v * 1_000))
    return int(round(v * 1_000_000)) if v < 1000 else int(round(v))     # "15 guaranteed" means $15M


def parse_contract_terms(text):
    """Contract terms from free text, or None unless both a length and a total are found ("40" alone is None).
    "3 years, 40 million, 15 guaranteed" -> {"years": 3, "total": 40000000, "guaranteed": 15000000, "bonus": 0}."""
    if not text or not text.strip():
        return None
    years, span = None, None
    m = _YEARS.search(text)
    if m:
        years, span = int(m.group(1)), m.span()
    else:
        m = _WORD_YEARS_RE.search(text)
        if m:
            years, span = _WORD_YEARS[m.group(1).lower()], m.span()
    if years is None or not 1 <= years <= MAX_YEARS:
        return None
    rest = text[:span[0]] + " " * (span[1] - span[0]) + text[span[1]:]
    total = guaranteed = bonus = None
    for a in _AMOUNT.finditer(rest):
        if not a.group(1) or not re.search(r"\d", a.group(1)):
            continue
        dollars = _dollars(a.group(1), a.group(2))
        after, before = rest[a.end():a.end() + 20], rest[max(0, a.start() - 20):a.start()]
        if _GUARANTEED.search(after) or _GUARANTEED_BEFORE.search(before):
            guaranteed = guaranteed if guaranteed is not None else dollars
        elif _BONUS.search(after) or _BONUS_BEFORE.search(before):
            bonus = bonus if bonus is not None else dollars
        elif total is None:
            total = dollars
    if total is None or total <= 0 or total > MAX_CONTRACT_TOTAL:
        return None
    guaranteed, bonus = guaranteed or 0, bonus or 0
    if guaranteed > total or bonus > total:
        return None
    return {"years": years, "total": total, "guaranteed": guaranteed, "bonus": bonus}


# ------------------------------------------------------------------------------------------- model fallback ---

def _prompt(text, schema, choices):
    fields = []
    for name, spec in schema.items():
        t = spec.get("type")
        if t == "number":
            fields.append(f'"{name}": a number from {spec.get("min")} to {spec.get("max")}')
        elif t == "id":
            fields.append(f'"{name}": one id from the list')
        elif t == "ids":
            fields.append(f'"{name}": a list of ids from the list')
        elif t == "enum":
            fields.append(f'"{name}": one of {", ".join(map(str, spec.get("values", ())))}')
    return "\n".join([
        "Turn this front-office message into structured terms. Use only the ids listed; never invent one.",
        f"Message: {text}",
        "Ids you may use (JSON): " + json.dumps(list(choices or []), default=str),
        "Fields: " + "; ".join(fields),
        'Answer with JSON only. If the message doesn\'t state terms, answer {"none": true}.'])


def _valid(value, spec, allowed):
    t = spec.get("type")
    if t == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        lo, hi = spec.get("min"), spec.get("max")
        return (lo is None or value >= lo) and (hi is None or value <= hi)
    if t == "id":
        return value in allowed
    if t == "ids":
        return isinstance(value, list) and bool(value) and all(v in allowed for v in value)
    if t == "enum":
        return value in spec.get("values", ())
    return False


def parse_with_llm(llm, text, schema, choices, budget_s=None):
    """The model's reading of text as {field: value}, or None. schema: {field: {"type": "number", "min", "max"} |
    {"type": "id"} | {"type": "ids"} | {"type": "enum", "values"}; "optional": True}. choices: the ids the model may
    use. Any unknown id, out-of-range number or missing required field -> None."""
    if llm is None or not text or not schema:
        return None
    from engines.inbox.voice import INBOX_LLM_BUDGET_S, call_with_budget, parse_json
    data = parse_json(call_with_budget(llm, _prompt(text, schema, choices),
                                       INBOX_LLM_BUDGET_S if budget_s is None else budget_s))
    if not data or data.get("none"):
        return None
    allowed = list(choices or [])
    out = {}
    for name, spec in schema.items():
        if name not in data or data[name] is None:
            if spec.get("optional"):
                continue
            return None
        if not _valid(data[name], spec, allowed):
            return None
        out[name] = data[name]
    return out


# -------------------------------------------------------------------------------------------------- summary ---

def money(v):
    """$40.0M, $750K, $0."""
    v = int(v or 0)
    if v == 0:
        return "$0"
    if abs(v) >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if abs(v) >= 1_000:
        return f"${v / 1_000:.0f}K"
    return f"${v}"


def draft_summary(option_id, payload):
    """One line JR confirms: "Offer 3 years, $40.0M total, $15.0M guaranteed, $0 bonus"."""
    p = dict(payload or {})
    if {"years", "total"} <= set(p):
        verb = "Offer" if option_id in (None, "MAKE_OFFER") else option_id.replace("_", " ").capitalize()
        return (f"{verb} {p['years']} year{'s' if int(p['years']) != 1 else ''}, {money(p['total'])} total, "
                f"{money(p.get('guaranteed'))} guaranteed, {money(p.get('bonus'))} bonus")
    if option_id == "PROMISE" and "slot" in p:
        depth = p.get("max_depth", 1)
        role = "start" if int(depth) == 1 else f"be no lower than {p['slot']}{depth}"
        when = f" by week {p['due_week']}" if p.get("due_week") is not None else ""
        return f"Promise: you'll {role} at {p['slot']}{when}"
    if option_id == "PROPOSE" and any(k in p for k in ("direction", "emphasis", "playing_time")):
        parts = [f"{k.replace('_', ' ')} {str(p[k]).lower()}" for k in ("direction", "emphasis", "playing_time")
                 if p.get(k)]
        return "Propose: " + ", ".join(parts)
    label = (option_id or "Message").replace("_", " ").capitalize()
    if not p:
        return label
    return label + ": " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in p.items())
