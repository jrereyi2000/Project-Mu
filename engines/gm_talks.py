"""
Two-GM CPU trade talks (docs/in_progress/TRADE_NEGOTIATIONS_PLAN.md §2.3; PROJECT_SPEC §2.17 Talks, §3.3f).

`run_week` hands this module the deals its search found and both GMs accept; instead of storing each one as a
PROPOSED proposal and waiting for JR (who, 2026-2033, has no team), the two GMs talk it out over one or more
turns. The numbers stay with the engines: `gm_negotiation.judge`, `gm_negotiation.counter_options` and
`gm_trade_engine.check_legality` build and check every option, and the persona policy (`policy_choice`) or the
model (`llm_choose`) only picks one of them. A completed deal is stored APPROVED with flags `talks:<id>` and
`auto_approved` (the league office writes it, and it retries instead of expiring while that is pending) plus the
flags the stored path gave the pick (`shape:`, `stage:`, `release:`, …; a resumed talk carries none); a breakdown
is stored STALLED (decided for pacing, but it never bars
its players the way a JR REJECTED proposal does). A CLOSE-mood talk may carry over one week; the next run resumes
it first. Some turns leak to the newsroom: `leak_events` draws a leak from the GMs' sliders and traits, stored in
`gm_trade_leaks`, positions only unless the leaker names the players he receives. Only AGREED CPU deals publish
their transcript (`publish_talk`, `talk_json`).

  python3 -m engines.gm_talks --show <id>
  python3 -m engines.gm_talks --replay-week [--season S --week W] [--dry-run] [--no-llm]
"""
import argparse
import functools
import json
import random
import re
import threading
from dataclasses import dataclass

TURNS_PER_RUN = 4           # turns one run plays per talk (estimates, tuned on a replay: plan §3 R2)
TALK_MAX_TURNS = 8          # turns a talk may ever play
MAX_CARRY = 1               # a talk may carry over once (D5)
CARRY_P_CLOSE = 0.6
ANCHOR_BASE = 0.2
SQUEEZE_BASE = 0.3
OPTION_K = 3
LEAK_BASE = 0.12
STAGE_W = {"OPENED": 0.6, "CLOSE": 1.0, "STALLED": 1.3}


# --------------------------------------------------------------- lazy imports ----
def _ge():
    from engines import gm_trade_engine
    return gm_trade_engine


def _gn():
    from engines import gm_negotiation
    return gm_negotiation


def _abbr(books, tid):
    return _ge().abbr(books, tid)


def _gm(books, tid):
    return books.get("gm", {}).get(tid) or {}


def _team_abbrs(books):
    return {t: i["abbr"] for t, i in books.get("teams", {}).items()}


# --------------------------------------------------------------- options ----
@dataclass
class Option:
    """One legal move a chooser may pick. assets are engine assets (from/to team ids); su/sg are the acting GM's
    and the other side's surplus (never public)."""
    key: str
    action: str
    posture: str | None
    assets: list | None
    su: int | None
    sg: int | None


def _as_dict(o):
    return o if isinstance(o, dict) else {"key": o.key, "action": o.action, "posture": o.posture,
                                          "assets": o.assets, "su": o.su, "sg": o.sg}


def _sliders(persona):
    s = persona.get("sliders") or {}
    return {k: float(s.get(k, persona.get(k, 5)) or 5)
            for k in ("temperament", "sociability", "confidence", "openness", "warmth", "drive")}


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _initiator(talk):
    return talk.get("initiator_team_id") or talk.get("user_team_id")


def _patience(talk, actor):
    return talk.get("patience", 0) if actor == _initiator(talk) else talk.get("patience_left", 0)


def _counter_counts(talk):
    return dict(talk.get("counter_counts") or {})


def _pick_downgrades(b, offer, initiator):
    """Outgoing picks swapped for a later-round pick the initiator owns in the same draft (R4 -> R5), each variant
    keeping every other leg. A pick already sent in the offer is never reused."""
    sent = {_ge().asset_key(x) for x in offer}
    out = []
    for x in offer:
        if x["type"] != "PICK" or x["from"] != initiator:
            continue
        draft, rnd, _ = x["pick"]
        for (ds, rd, orig), owner in sorted(b.get("picks", {}).items()):
            if owner != initiator or ds != draft or rd <= rnd:
                continue
            later = {"type": "PICK", "pick": (ds, rd, orig), "from": initiator, "to": x["to"]}
            if _ge().asset_key(later) in sent:
                continue
            out.append([later if y is x else y for y in offer])
    return out


def _options_for_opening(b, valuer, talk, actor, offer):
    """The initiating GM's opening: FIRM = the deal as offered; ANCHOR = up to K-1 legal variants that raise his
    surplus (a pre-validated option from the unilateral search, dropping one of his non-core sends, adding one of
    the other side's free assets, or downgrading one of his outgoing picks to a later pick he owns in the same
    draft). Every candidate is checked with check_legality(d, b, valuer), so an ANCHOR is always a legal offer."""
    initiator, other = _initiator(talk), talk["gm_team_id"]
    offer = list(offer)
    posture = talk.get("opening_posture")
    seeds = [o for o in (talk.get("opening_options") or []) if isinstance(o, dict) and o.get("assets")]
    options, seen = [], set()

    def make(pose, assets):
        assets = list(assets or [])
        if not assets:
            return None
        h = _ge().deal_hash(initiator, other, assets)
        if h in seen:
            return None
        d = {"a": initiator, "b": other, "assets": assets}
        if not _ge().legal(_ge().check_legality(d, b, valuer)):
            return None
        seen.add(h)
        su, sg = _gn().surpluses(d, valuer, initiator)
        return Option(None, "OFFER", pose, assets, su, sg)

    # FIRM first: the unilateral search's pre-validated firm candidate, else the deal's own opening. An ANCHOR
    # opening is the offer itself, not the firm base, so it is added to the anchors below.
    firm_seed = next((o for o in seeds if (o.get("posture") or "FIRM") != "ANCHOR"), None)
    firm_assets = firm_seed["assets"] if firm_seed is not None else (None if posture == "ANCHOR" else offer)
    firm = make("FIRM", firm_assets) if firm_assets else None
    if firm is None:
        firm = make("FIRM", offer)
    if firm is None:                                                       # nothing legal to open on
        return []
    firm.key = "FIRM"
    options.append(firm)
    firm_su = firm.su

    anchors = []
    for o in seeds:                                                        # the search's anchors, pre-validated
        if (o.get("posture") or "FIRM") == "ANCHOR":
            row = make("ANCHOR", o["assets"])
            if row is not None:
                anchors.append(row)
    if posture == "ANCHOR":                                                # the personality's chosen opening
        row = make("ANCHOR", offer)
        if row is not None and row.su >= firm_su:
            anchors.insert(0, row)
    deal = {"a": initiator, "b": other, "assets": offer}
    core = _gn().core_asset(deal, valuer, initiator)                       # what the other GM values most from him
    ck = _ge().asset_key(core) if core else None
    variants = []
    for x in offer:                                                        # drop one of his non-core sends
        if x["from"] == initiator and _ge().asset_key(x) != ck:
            variants.append([y for y in offer if y is not x])
    for p in _gn().user_pool(b, valuer, deal, other, initiator):           # ask for one more of the other side's assets
        variants.append(list(offer) + [p])
    variants += _pick_downgrades(b, offer, initiator)                      # send a later-round pick instead
    for assets in variants:
        row = make("ANCHOR", assets)
        if row is not None and row.su > firm_su:
            anchors.append(row)
    anchors.sort(key=lambda o: o.su)                                       # the mildest ask first
    offer_h = _ge().deal_hash(initiator, other, offer)
    chosen = next((o for o in anchors if _ge().deal_hash(initiator, other, o.assets) == offer_h), None) \
        if posture == "ANCHOR" else None
    ordered = ([chosen] if chosen is not None else []) + [o for o in anchors if o is not chosen]
    for i, row in enumerate(ordered[:OPTION_K - 1], 1):
        row.key = f"ANCHOR{i}"
        options.append(row)
    return options


def _options_for_response(b, valuer, talk, actor, offer):
    """A GM answering the offer on the table: ACCEPT (+ SQUEEZE) when it clears his floor, else up to K counters
    (MEET = softest, HOLD = dearest) plus REJECT; patience at zero leaves WALK_AWAY alone."""
    initiator, other = _initiator(talk), talk["gm_team_id"]
    offerer = initiator if actor == other else other
    deal = {"a": offerer, "b": actor, "assets": list(offer)}
    reserve = _gn().reserve_for(talk["season"], talk["id"], _gm(b, actor).get("aggressiveness", 0.5))
    j = _gn().judge(b, valuer, deal, offerer, actor, reserve)
    patience = _patience(talk, actor)
    left = patience - (2 if j["mood"] == "INSULTED" else 1)
    counters_made = _counter_counts(talk).get(actor, 0)
    if j["ok"]:
        options = [Option("ACCEPT", "ACCEPT", "MEET", list(offer), j["sg"], j["su"])]
        if patience > 1:
            opts = _gn().counter_options(b, valuer, deal, offerer, actor, reserve, counters_made)
            if opts:
                c = opts[-1]                                          # the dearest: squeezing above his floor
                options.append(Option("SQUEEZE", "COUNTER", "SQUEEZE", c["assets"], c["sg"], c["su"]))
        return options
    if left <= 0:
        return [Option("WALK_AWAY", "WALK_AWAY", None, None, None, None)]
    opts = _gn().counter_options(b, valuer, deal, offerer, actor, reserve, counters_made)
    options = []
    for i, c in enumerate(opts):
        key = "MEET" if i == 0 else ("HOLD" if i == len(opts) - 1 else f"HOLD{i + 1}")
        posture = "MEET" if i == 0 else "HOLD"
        options.append(Option(key, "COUNTER", posture, c["assets"], c["sg"], c["su"]))
    options.append(Option("REJECT", "REJECT", None, None, None, None))
    return options


def options_for(b, valuer, talk, actor, offer):
    """Every legal move `actor` may make this turn (pure). `offer` is the package on the table (engine assets);
    pass `talk["opening"] = True` for the initiating GM's first turn."""
    if talk.get("opening") and actor == _initiator(talk):
        return _options_for_opening(b, valuer, talk, actor, offer)
    return _options_for_response(b, valuer, talk, actor, offer)


# --------------------------------------------------------------- policy ----
def policy_choice(options, persona, rng):
    """The seeded persona policy's choice (pure): `key` of one of `options`. Opening anchors on aggressiveness and
    confidence; a GM above his floor squeezes on aggressiveness and drive; a counter meets or holds on warmth and
    temperament, with CAP_MIND holding more; a GRUDGE_HOLDER who was insulted may reject."""
    opts = [_as_dict(o) for o in options]
    if not opts:
        return None
    if len(opts) == 1:
        return opts[0]["key"]
    sl = _sliders(persona)
    traits = set(persona.get("traits") or [])
    agg = float(persona.get("aggressiveness", 0.5) or 0.5)
    offers = [o for o in opts if o["action"] == "OFFER"]
    if offers:
        firm = next((o for o in offers if o["posture"] != "ANCHOR"), offers[0])
        anchors = [o for o in offers if o["posture"] == "ANCHOR"]
        p_anchor = _clamp(ANCHOR_BASE + 0.4 * agg + 0.04 * (sl["confidence"] - 5) - 0.03 * (sl["warmth"] - 5),
                          0.05, 0.85)
        if anchors and rng.random() < p_anchor:
            return min(anchors, key=lambda o: o["su"] if o["su"] is not None else 0)["key"]
        return firm["key"]
    accept = next((o for o in opts if o["action"] == "ACCEPT"), None)
    if accept:
        squeeze = next((o for o in opts if o["posture"] == "SQUEEZE"), None)
        if squeeze:
            p_squeeze = SQUEEZE_BASE * agg * (1 + 0.05 * (sl["drive"] - 5))
            if rng.random() < p_squeeze:
                return squeeze["key"]
        return accept["key"]
    meet = next((o for o in opts if o["posture"] == "MEET"), None)
    hold = next((o for o in opts if o["posture"] == "HOLD"), None)
    reject = next((o for o in opts if o["action"] == "REJECT"), None)
    if not meet and not hold:
        return (reject or opts[0])["key"]
    p_meet = _clamp(0.5 + 0.05 * (sl["warmth"] - 5) - 0.05 * (sl["temperament"] - 5), 0.1, 0.9)
    if "CAP_MIND" in traits:
        p_meet = _clamp(p_meet - 0.1, 0.1, 0.9)
    if reject and "GRUDGE_HOLDER" in traits and persona.get("insulted") and meet and rng.random() < 0.5:
        return reject["key"]
    if meet and rng.random() < p_meet:
        return meet["key"]
    return (hold or reject or meet)["key"]


# --------------------------------------------------------------- model ----
def _template_line(persona, option, sheet=None):
    name = persona.get("gm_name") or "The GM"
    action = _as_dict(option)["action"] if option else "COUNTER"
    if action == "ACCEPT":
        return f"{name}: We can do that. Send it over and we'll get it through on our end."
    if action == "REJECT":
        return f"{name}: I can't find a version of this that works for us."
    if action == "WALK_AWAY":
        return f"{name}: We've gone back and forth enough. Let's pick this up another time."
    if action == "OFFER":
        return f"{name}: We have an offer on the table. Here's where we'd start."
    return f"{name}: Here's where we'd get it done. Take a look at the package."


def _leg_line(a):
    if a.get("type") == "PLAYER":
        return f"player {a.get('player_id')} from {a.get('from')} to {a.get('to')}"
    pick = a.get("pick")
    ds, rd, orig = pick if pick else (a.get("draft_season"), a.get("round"), a.get("original_team"))
    return f"{ds} R{rd} ({orig}) from {a.get('from')} to {a.get('to')}"


def _choice_prompt(sheet, persona, options, history):
    sl = _sliders(persona)
    lines = [f"You are {persona.get('gm_name') or 'a general manager'} of a simulated NFL team, in a trade talk "
             f"with another general manager.",
             f"Voice: {persona.get('voice') or 'plain'}."]
    try:
        from newsroom.personas import slider_sentence, load_traits
        s = slider_sentence(persona.get("sliders") or sl)
        if s:
            lines.append(f"Disposition: {s}")
        cat = load_traits()
        for tid in persona.get("traits") or []:
            entry = cat.get(tid)
            if entry:
                lines.append(f"- {entry.get('name', tid)}: {entry.get('prompt', '')}")
    except Exception:
        pass
    lines += ["FACT SHEET (JSON; the only facts you may state):", json.dumps(sheet, indent=1, default=str)]
    for h in history or []:
        lines.append(f"{h.get('team', '?')} {h.get('action', '')}: {h.get('message') or ''}")
    lines.append("LEGAL MOVES (pick one of these keys; never invent another):")
    for o in options:
        o = _as_dict(o)
        assets = ", ".join(_leg_line(a) for a in (o.get("assets") or [])) or "nothing on the table"
        lines.append(f"- [{o['key']}] {o['action']} {o.get('posture') or ''}: {assets}")
    lines += ["Write 2-4 sentences in your own voice. Never state a number, dollar amount, player, team or contract "
              "fact that isn't on the fact sheet, and never say the trade is final or completed.",
              'Answer with JSON only: {"choice": "<key>", "message": "..."}']
    return "\n".join(lines)


def _parse_choice(raw):
    m = re.search(r"\{.*\}", raw or "", re.S)
    try:
        d = json.loads(m.group(0) if m else (raw or ""))
    except (ValueError, TypeError, AttributeError):
        return None, ""
    if not isinstance(d, dict):
        return None, ""
    return d.get("choice"), str(d.get("message") or "").strip()


def _parse_seed(persona, history):
    return f"llm:{persona.get('gm_id') or persona.get('gm_name') or 'gm'}:{len(history or [])}"


def llm_choose(llm, sheet, persona, options, history):
    """(key, text, source): one model call picks among `options` and writes the line. A key outside `options`
    falls back to the policy (source POLICY); a message that fails `validate_rationale` falls back to the template.
    With llm=None the policy picks and the template speaks."""
    opts = [_as_dict(o) for o in options]
    keys = {o["key"] for o in opts}
    if not opts:
        return None, None, None
    if llm is None:
        key = policy_choice(opts, persona, random.Random(_parse_seed(persona, history)))
        opt = next((o for o in opts if o["key"] == key), opts[0])
        return key, _template_line(persona, opt, sheet), "POLICY"
    from engines.gm_llm import call
    from engines.gm_rationale import validate_rationale
    raw = call(llm, _choice_prompt(sheet, persona, opts, history))
    key, text = _parse_choice(raw)
    if key not in keys:
        pkey = policy_choice(opts, persona, random.Random(_parse_seed(persona, history)))
        opt = next((o for o in opts if o["key"] == pkey), opts[0])
        good = bool(text) and validate_rationale(text, sheet, None)[0]
        return pkey, (text if good else _template_line(persona, opt, sheet)), "POLICY"
    opt = next(o for o in opts if o["key"] == key)
    if text and validate_rationale(text, sheet, None)[0]:
        return key, text, "LLM"
    return key, _template_line(persona, opt, sheet), "LLM"


def call_with_timeout(llm, prompt, seconds):
    """gm_llm.call in a worker thread; None after `seconds` (the caller uses its template)."""
    if llm is None:
        return None
    if not seconds:
        from engines.gm_llm import call
        return call(llm, prompt)
    from engines.gm_llm import call
    result = {}

    def run():
        result["value"] = call(llm, prompt)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(seconds)
    return result.get("value")


# --------------------------------------------------------------- DB helpers ----
_TALK_COLS = ("id", "season", "week", "trade_window", "offseason_stage", "user_team_id", "gm_team_id", "kind",
              "initiator_team_id", "source_proposal_id", "carried_weeks", "published_at", "status", "status_reason",
              "patience", "patience_left", "proposal_id", "trigger_kind", "created_at", "updated_at", "flags")
_TURN_COLS = ("turn_no", "actor", "action", "assets", "surplus_user", "surplus_gm", "mood", "legality", "message",
              "message_source", "actor_team_id", "options", "choice", "choice_source", "posture", "created_at")


def _talk(cur, nid):
    cur.execute(f"SELECT {', '.join(_TALK_COLS)} FROM gm_trade_negotiations WHERE id = %s", (nid,))
    r = cur.fetchone()
    return dict(zip(_TALK_COLS, r)) if r else None


def _turns(cur, nid):
    cur.execute(f"""SELECT {', '.join(_TURN_COLS)} FROM gm_trade_negotiation_turns WHERE negotiation_id = %s
                    ORDER BY turn_no""", (nid,))
    return [dict(zip(_TURN_COLS, r)) for r in cur.fetchall()]


def _add_turn(cur, nid, actor, action, actor_team_id, assets=None, su=None, sg=None, mood=None, legality=None,
              message=None, source=None, options=None, choice=None, choice_source=None, posture=None, books=None):
    cur.execute("SELECT COALESCE(max(turn_no), 0) + 1 FROM gm_trade_negotiation_turns WHERE negotiation_id = %s",
                (nid,))
    n = cur.fetchone()[0]
    from engines.front_office import _json_asset
    js = None if assets is None else json.dumps([_json_asset(x, _team_abbrs(books or {})) for x in assets])
    cur.execute("""INSERT INTO gm_trade_negotiation_turns (negotiation_id, turn_no, actor, action, actor_team_id,
                       assets, options, choice, choice_source, posture, surplus_user, surplus_gm, mood, legality,
                       message, message_source)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (nid, n, actor, action, actor_team_id, js,
                 json.dumps(options, default=str) if options is not None else None, choice, choice_source, posture,
                 su, sg, mood, json.dumps(legality) if legality else None, message, source))
    cur.execute("UPDATE gm_trade_negotiations SET updated_at = now() WHERE id = %s", (nid,))
    return n


def _set_status(cur, nid, status, reason=None, proposal_id=None):
    cur.execute("""UPDATE gm_trade_negotiations SET status = %s,
                       status_reason = COALESCE(%s, status_reason), proposal_id = COALESCE(%s, proposal_id),
                       source_proposal_id = COALESCE(%s, source_proposal_id), updated_at = now()
                   WHERE id = %s""", (status, reason, proposal_id, proposal_id, nid))


def _set_patience(cur, talk, actor, value):
    col = "patience" if actor == _initiator(talk) else "patience_left"
    cur.execute(f"UPDATE gm_trade_negotiations SET {col} = %s, updated_at = now() WHERE id = %s",
                (max(0, value), talk["id"]))
    talk[col] = max(0, value)


def _set_carried(cur, talk):
    cur.execute("UPDATE gm_trade_negotiations SET carried_weeks = carried_weeks + 1, updated_at = now() "
                "WHERE id = %s", (talk["id"],))
    talk["carried_weeks"] = (talk.get("carried_weeks") or 0) + 1


def _engine_assets(books, stored):
    """Stored turn assets (team abbreviations) -> engine assets (team ids)."""
    ids = {v["abbr"]: t for t, v in books.get("teams", {}).items()}
    out = []
    for a in stored or []:
        frm = ids.get(a.get("from"), a.get("from"))
        to = ids.get(a.get("to"), a.get("to"))
        if a.get("type") == "PLAYER":
            out.append({"type": "PLAYER", "player_id": a.get("player_id"), "from": frm, "to": to})
        else:
            orig = ids.get(a.get("original_team"), a.get("original_team"))
            out.append({"type": "PICK", "pick": (a.get("draft_season"), a.get("round"), orig),
                        "from": frm, "to": to})
    return out


# --------------------------------------------------------------- talks ----
def _opening_choice(options, deal, persona, rng):
    """The initiating GM's Turn 1 move. A deal from the unilateral search carries `opening_posture` (FIRM or
    ANCHOR) and `opening_options`: open with that personality-selected option (the deal's own assets when it is in
    the list), so a lowball opens as an ANCHOR instead of being relabelled FIRM. A deal without the new keys keeps
    the old policy choice."""
    if not options:
        return None
    posture = deal.get("opening_posture")
    if posture in ("FIRM", "ANCHOR"):
        h = _ge().deal_hash(deal["a"], deal["b"], deal["assets"])
        same = [o for o in options if _ge().deal_hash(deal["a"], deal["b"], o.assets) == h]
        pool = same or [o for o in options if o.posture == posture]
        if pool:
            return pool[0]
    key = policy_choice(options, persona, rng)
    return next((o for o in options if o.key == key), options[0])


def open_talk(cur, b, valuer, deal, rng):
    """Insert a CPU negotiation for a deal both GMs accept and play the initiator's opening turn. A deal from the
    unilateral search opens with its personality-selected offer (`opening_posture`, seeded with the search's
    `opening_options`); a legacy deal without those keys keeps the policy's FIRM/ANCHOR choice. Returns the talk."""
    season, week = b["season"], b["week"]
    window = b["window"] if b.get("window") in ("IN_SEASON", "OFFSEASON") else "IN_SEASON"
    stage = _ge().read_stage(cur, season) if window == "OFFSEASON" else None
    a, other = deal["a"], deal["b"]
    p_a = _gn().patience_for(_gm(b, a).get("aggressiveness", 0.5))
    p_b = _gn().patience_for(_gm(b, other).get("aggressiveness", 0.5))
    cur.execute("""INSERT INTO gm_trade_negotiations (season, week, trade_window, offseason_stage, user_team_id,
                       gm_team_id, kind, initiator_team_id, patience, patience_left, trigger_kind, flags)
                   VALUES (%s, %s, %s, %s, %s, %s, 'CPU', %s, %s, %s, %s, %s) RETURNING id""",
                (season, week, window, stage, a, other, a, p_a, p_b, deal.get("trigger") or "NEED",
                 list(deal.get("flags") or [])))
    nid = cur.fetchone()[0]
    talk = _talk(cur, nid)                           # the stored-path flags live on the row (2027_02_12)
    opener = dict(talk, opening=True, opening_posture=deal.get("opening_posture"),
                  opening_options=deal.get("opening_options"))
    options = options_for(b, valuer, opener, a, deal["assets"])
    if not options:                                  # an asset that moved between the search and the talk
        su, sg = _gn().surpluses({"a": a, "b": other, "assets": deal["assets"]}, valuer, a)
        options = [Option("FIRM", "OFFER", deal.get("opening_posture") or "FIRM", list(deal["assets"]), su, sg)]
    opt = _opening_choice(options, deal, _gm(b, a), random.Random(f"talk:{season}:{nid}:1"))
    _add_turn(cur, nid, "GM_A", "OFFER", a, assets=opt.assets, su=opt.su, sg=opt.sg,
              message=_template_line(_gm(b, a), opt), source="TEMPLATE",
              options=[{"key": o.key, "action": o.action, "assets": o.assets, "su": o.su, "sg": o.sg}
                       for o in options],
              choice=opt.key, choice_source="POLICY", posture=opt.posture, books=b)
    return talk


def _sheet(b, offerer, responder, offer_assets, counter_assets=None):
    return _gn().message_sheet(b, offerer, responder, offer_assets, counter_assets)


def _history(turns, talk, books):
    out = []
    for t in turns[-6:]:
        tid = _turn_actor_team(t, talk)
        out.append({"team": _abbr(books, tid) if tid else "?", "action": t.get("action"),
                    "message": t.get("message")})
    return out


def _turn_actor_team(t, talk):
    if t.get("actor_team_id"):
        return t["actor_team_id"]
    if t.get("actor") in ("JR", "GM_A"):
        return _initiator(talk)
    return talk["gm_team_id"]


def _choose(b, talk, actor, offerer, offer_assets, options, turns, rng, llm):
    """(option, text, source) for this turn."""
    persona = dict(_gm(b, actor))
    if any(t.get("mood") == "INSULTED" and _turn_actor_team(t, talk) == actor for t in turns):
        persona["insulted"] = True
    if llm is not None:
        sheet = _sheet(b, offerer, actor, offer_assets)
        key, text, source = llm_choose(llm, sheet, persona, options, _history(turns, talk, b))
        opt = next((o for o in options if o.key == key), None)
        if opt is not None:
            return opt, text, source
    key = policy_choice(options, persona, random.Random(f"talk:{talk['season']}:{talk['id']}:{len(turns) + 1}"))
    opt = next((o for o in options if o.key == key), options[0])
    return opt, _template_line(persona, opt), "POLICY"


def _last_mood(turns):
    for t in reversed(turns):
        if t.get("mood"):
            return t["mood"]
    return None


def _window_allows_later(b, talk):
    if b.get("window") == "IN_SEASON":
        return b["week"] < _ge().TRADE_DEADLINE_WEEK
    if b.get("window") == "OFFSEASON":
        return (talk.get("offseason_stage") or 1) < len(_ge().OFFSEASON_STAGES)
    return False


def carry_allowed(talk, b, last_mood, rng):
    """May a talk at this run's turn limit stay OPEN for next week? Only from a CLOSE decline, once, and never past
    the deadline / last offseason stage."""
    if last_mood != "CLOSE":
        return False
    if (talk.get("carried_weeks") or 0) >= MAX_CARRY:
        return False
    if not _window_allows_later(b, talk):
        return False
    return rng.random() < CARRY_P_CLOSE


def _stall(cur, b, valuer, talk, offer_assets, reason, status="STALLED"):
    """Store the last offer on the table as a STALLED proposal (decided for pacing, no player barred) and end the
    talk with `status` (STALLED, or WALKED_AWAY). Returns the proposal id or None."""
    nid = talk["id"]
    talk["status"] = status                       # the dict follows the row (leaks read it)
    if not offer_assets:
        _set_status(cur, nid, status, reason)
        return None
    deal = {"a": _initiator(talk), "b": talk["gm_team_id"], "assets": offer_assets,
            "trigger": talk.get("trigger_kind") or "NEED"}
    rat = _ge().rationale_for(b, deal, None)
    flags = [f"talks:{nid}"] + list(talk.get("flags") or [])
    pid = None
    for v in (valuer, None):                     # an asset that moved may no longer be priceable
        try:
            pid = _ge().store(cur, b, deal, v, rat, flags, origin="ENGINE", status="STALLED",
                              trigger=deal.get("trigger"))
            break
        except Exception as e:
            print(f"[gm_talks] stall not stored: {e}", flush=True)
    _set_status(cur, nid, status, reason, proposal_id=pid)
    return pid


def _agree(cur, b, valuer, talk, offer_assets, llm):
    """Store the agreed deal APPROVED (flags talks:<id> + auto_approved) and agree the talk. Returns the id."""
    nid = talk["id"]
    deal = {"a": _initiator(talk), "b": talk["gm_team_id"], "assets": offer_assets,
            "trigger": talk.get("trigger_kind") or "NEED"}
    checks = _ge().check_legality(deal, b, valuer)
    rat = _ge().rationale_for(b, deal, llm)
    flags = ([f"talks:{nid}", "auto_approved"] + list(talk.get("flags") or [])
             + _ge().release_flags(checks, b) + _ge().llm_flags(rat))
    pid = _ge().store(cur, b, deal, valuer, rat, flags, origin="ENGINE", status="APPROVED",
                      trigger=deal.get("trigger"))
    if pid is None:
        _set_status(cur, nid, "STALLED", "this deal is already stored")
        return None
    cur.execute("UPDATE gm_trade_proposals SET approved_at = now() WHERE id = %s", (pid,))
    _set_status(cur, nid, "AGREED", "the two GMs agreed", proposal_id=pid)
    return pid


def _table_exists(cur, name):
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (name,))
    return bool(cur.fetchone()[0])


def _store_leak(cur, leak):
    if not _table_exists(cur, "gm_trade_leaks"):
        return False
    cur.execute("SELECT 1 FROM gm_trade_leaks WHERE negotiation_id = %s AND season = %s AND week = %s",
                (leak.negotiation_id, leak.season, leak.week))
    if cur.fetchone():
        return False
    cur.execute("""INSERT INTO gm_trade_leaks (negotiation_id, turn_no, season, week, leaker_team_id, stage,
                       named_player_ids, detail, insider_key)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (leak.negotiation_id, leak.turn_no, leak.season, leak.week, leak.leaker_team_id, leak.stage,
                 leak.named_player_ids, json.dumps(leak.detail), leak.insider_key))
    return True


def record_leaks(cur, talk, turn, b, rng, turns=None):
    """Draw this turn's leaks and store them; the public seam for callers outside the loop (the inbox's GM_TALK).
    Returns how many rows were stored. Pass `turns` (the transcript so far) so the OPENED stage lands on the first
    turn a GM speaks."""
    return sum(bool(_store_leak(cur, leak)) for leak in leak_events(talk, turn, b, rng, turns))


def _maybe_leak(cur, talk, turn, b, rng, turns=None):
    record_leaks(cur, talk, turn, b, rng, turns)


def advance_talk(cur, b, valuer, talk, rng, llm):
    """Play the talk on from its stored turns until AGREED, WALKED_AWAY, a run's `TURNS_PER_RUN` turns, or
    `TALK_MAX_TURNS` in all; at a run limit a CLOSE talk may carry over once, else it stalls. Returns the status."""
    nid = talk["id"]
    if talk.get("status") != "OPEN":
        return talk["status"]
    initiator, other = _initiator(talk), talk["gm_team_id"]
    turns = _turns(cur, nid)
    if not turns:                                 # a talk with no opening cannot advance (never opened by us)
        return talk.get("status")
    talk["counter_counts"] = {t: sum(1 for x in turns if x["action"] == "COUNTER"
                                     and _turn_actor_team(x, talk) == t) for t in (initiator, other)}
    played = 0
    while talk.get("status") == "OPEN" and played < TURNS_PER_RUN and len(turns) < TALK_MAX_TURNS:
        last = turns[-1]
        last_actor = _turn_actor_team(last, talk)
        actor = other if last_actor == initiator else initiator
        offer_assets = _engine_assets(b, last.get("assets"))
        options = options_for(b, valuer, talk, actor, offer_assets)
        opt, text, source = _choose(b, talk, actor, last_actor, offer_assets, options, turns, rng, llm)
        # message_source is JR | LLM | TEMPLATE; the chooser's POLICY/LLM source lives in choice_source
        msg_source = "TEMPLATE" if source == "POLICY" else source
        label = "GM_A" if actor == initiator else "GM"
        opts_json = [{"key": o.key, "action": o.action, "assets": o.assets, "su": o.su, "sg": o.sg}
                     for o in options]
        if opt.action == "ACCEPT":
            n = _add_turn(cur, nid, label, "ACCEPT", actor, assets=offer_assets, su=opt.su, sg=opt.sg,
                          message=text, source=msg_source, options=opts_json, choice=opt.key,
                          choice_source=source, posture=opt.posture, books=b)
            turns = _turns(cur, nid)
            pid = _agree(cur, b, valuer, talk, offer_assets, llm)
            talk["status"] = "AGREED" if pid else "STALLED"
            played += 1
            break
        if opt.action == "WALK_AWAY":
            _add_turn(cur, nid, label, "WALK_AWAY", actor, message=text, source=msg_source, options=opts_json,
                      choice=opt.key, choice_source=source, books=b)
            _set_patience(cur, talk, actor, 0)
            _set_status(cur, nid, "WALKED_AWAY", "the GM ran out of patience")
            talk["status"] = "WALKED_AWAY"
            turns = _turns(cur, nid)
            _maybe_leak(cur, talk, dict(turns[-1], assets=offer_assets), b, rng, turns)
            _stall(cur, b, valuer, talk, offer_assets, "the GM ran out of patience", status="WALKED_AWAY")
            played += 1
            break
        # COUNTER or REJECT: a decline costs patience
        reserve = _gn().reserve_for(talk["season"], nid, _gm(b, actor).get("aggressiveness", 0.5))
        j = _gn().judge(b, valuer, {"a": last_actor, "b": actor, "assets": offer_assets},
                        last_actor, actor, reserve)
        cost = 2 if j["mood"] == "INSULTED" else 1
        left = max(0, _patience(talk, actor) - cost)
        _set_patience(cur, talk, actor, left)
        if opt.action == "COUNTER":
            _add_turn(cur, nid, label, "COUNTER", actor, assets=opt.assets, su=opt.su, sg=opt.sg, mood=j["mood"],
                      message=text, source=msg_source, options=opts_json, choice=opt.key, choice_source=source,
                      posture=opt.posture, books=b)
            talk["counter_counts"][actor] = talk["counter_counts"].get(actor, 0) + 1
            _set_status(cur, nid, "OPEN")
        else:                                              # REJECT
            _add_turn(cur, nid, label, "REJECT", actor, mood=j["mood"], message=text, source=msg_source,
                      options=opts_json, choice=opt.key, choice_source=source, books=b)
        turns = _turns(cur, nid)
        played += 1
        _maybe_leak(cur, talk, turns[-1], b, rng, turns)
        if left <= 0:
            _set_status(cur, nid, "WALKED_AWAY", "the GM ran out of patience")
            talk["status"] = "WALKED_AWAY"
            _stall(cur, b, valuer, talk, offer_assets, "the GM ran out of patience", status="WALKED_AWAY")
    if talk.get("status") != "OPEN":
        return talk["status"]
    limit = played >= TURNS_PER_RUN or len(turns) >= TALK_MAX_TURNS
    if not limit:
        return "OPEN"
    # "the last offer on the table": a REJECT (or any assetless turn) leaves the previous package standing
    offer_turn = next((t for t in reversed(turns) if t.get("assets")), None)
    last_offer = _engine_assets(b, offer_turn["assets"]) if offer_turn else []
    if len(turns) < TALK_MAX_TURNS and carry_allowed(talk, b, _last_mood(turns), rng):
        _set_status(cur, nid, "OPEN", "the sides are close; the talk carries over")
        _set_carried(cur, talk)
        return "OPEN"
    _stall(cur, b, valuer, talk, last_offer, "the sides could not get close enough")
    if turns:
        leak_turn = dict(turns[-1])
        if offer_turn is not None:
            leak_turn["assets"] = last_offer
        _maybe_leak(cur, talk, leak_turn, b, rng, turns)
    return "STALLED"


def _rejudge(b, valuer, talk, offer_assets):
    """Reason the resumed talk's last offer no longer works, or None. Illegal now / an asset moved elsewhere."""
    deal = {"a": _initiator(talk), "b": talk["gm_team_id"], "assets": offer_assets}
    for x in offer_assets:
        if x["type"] == "PLAYER":
            p = b.get("players", {}).get(x["player_id"])
            if p is None or p.get("team_id") != x["from"]:
                return "the deal stopped working"
        else:
            if b.get("picks", {}).get(tuple(x["pick"])) != x["from"]:
                return "the deal stopped working"
    if not _ge().legal(_ge().check_legality(deal, b, valuer)):
        return "the deal stopped working"
    return None


def resume_talks(cur, b, valuer, rng, llm, log=None):
    """Resume every OPEN CPU talk of this season/window before the week's search (P1-17), so a team already carrying
    a talk is in `rep["teams"]` and cannot open a second deal this week. Returns
    {"talks": [...], "agreed": [...], "stalled": [...], "teams": set()}."""
    log = log or (lambda *a, **k: None)
    rep = {"talks": [], "agreed": [], "stalled": [], "teams": set()}
    cur.execute("""SELECT id FROM gm_trade_negotiations
                   WHERE season = %s AND kind = 'CPU' AND status = 'OPEN' AND trade_window = %s ORDER BY id""",
                (b["season"], b.get("window") or "IN_SEASON"))
    for (nid,) in cur.fetchall():
        talk = _talk(cur, nid)
        if not talk:
            continue
        rep["teams"].update((_initiator(talk), talk["gm_team_id"]))
        turns = _turns(cur, nid)
        if turns:
            offer = _engine_assets(b, turns[-1].get("assets"))
            why = _rejudge(b, valuer, talk, offer)
            if why:
                _stall(cur, b, valuer, talk, offer, why)
                rep["stalled"].append(nid)
                log(f"  talk #{nid}: {why}; stalled")
                continue
        status = advance_talk(cur, b, valuer, talk, rng, llm)
        entry = {"id": nid, "status": status, "resumed": True}
        if status == "OPEN":
            entry["carried"] = True
        elif status == "AGREED":
            rep["agreed"].append(nid)
        else:
            rep["stalled"].append(nid)
        rep["talks"].append(entry)
        log(f"  talk #{nid}: {status}")
    return rep


def run_talks(cur, b, valuer, deals, rng, llm, log=None):
    """Open a talk for each new deal (the search's collected CPU picks). Resumption is `resume_talks`, which runs
    before the search. Returns {"talks": [...], "agreed": [...], "stalled": [...], "teams": set()}."""
    log = log or (lambda *a, **k: None)
    rep = {"talks": [], "agreed": [], "stalled": [], "teams": set()}
    for deal in deals or []:
        talk = open_talk(cur, b, valuer, deal, rng)
        rep["teams"].update((_initiator(talk), talk["gm_team_id"]))
        status = advance_talk(cur, b, valuer, talk, rng, llm)
        rep["talks"].append({"id": talk["id"], "status": status, "resumed": False})
        if status == "AGREED":
            rep["agreed"].append(talk["id"])
        elif status in ("STALLED", "WALKED_AWAY"):
            rep["stalled"].append(talk["id"])
        log(f"  talk #{talk['id']} {_abbr(b, _initiator(talk))} <-> {_abbr(b, talk['gm_team_id'])}: {status}")
    return rep


# --------------------------------------------------------------- leaks ----
@dataclass
class Leak:
    negotiation_id: int
    turn_no: int
    season: int
    week: int
    leaker_team_id: int
    stage: str
    named_player_ids: list
    detail: dict
    insider_key: str | None


@functools.lru_cache(maxsize=1)
def _insider_keys():
    try:
        from newsroom.personas import load_personas
        return tuple(k for k, p in load_personas().items() if "INSIDER" in (p.get("traits") or []))
    except Exception:
        return ()


def _leak_stage(talk, turn, turns=None):
    if turn.get("action") == "ACCEPT" or talk.get("status") == "AGREED":
        return None
    if talk.get("status") in ("STALLED", "WALKED_AWAY"):
        return "STALLED"
    if turns is not None:                              # the first turn a GM speaks is when talks opened
        gm_turns = [t for t in turns if t.get("actor") in ("GM", "GM_A")]
        if gm_turns and turn.get("turn_no") == gm_turns[0].get("turn_no"):
            return "OPENED"
    elif turn.get("action") == "OFFER" and turn.get("turn_no") == 1:
        return "OPENED"                                # without the transcript: a CPU talk's opening turn
    if turn.get("mood") == "CLOSE":
        return "CLOSE"
    return None


def leak_events(talk, turn, books, rng, turns=None):
    """Leaks a turn may produce (pure, at most one per talk per week): per GM p = LEAK_BASE x (0.5 +
    sociability/10) x (0.5 GUARDED) x (1.5 QUOTABLE) x STAGE_W[stage]. An aggressive leaker names the players he
    receives; otherwise positions only. `books` may carry "insider_relationships": {team_id: {insider_key}} to
    weight an insider who already knows that GM. `turns` is the talk's transcript so far: with it, the OPENED stage
    is the first turn a GM speaks (so JR's first offer in a USER talk doesn't consume it); without it, turn 1."""
    stage = _leak_stage(talk, turn, turns)
    if stage is None:
        return []
    initiator, other = _initiator(talk), talk["gm_team_id"]
    for tid in (initiator, other):
        gm = _gm(books, tid)
        p = LEAK_BASE * (0.5 + _sliders(gm)["sociability"] / 10)
        if "GUARDED" in (gm.get("traits") or []):
            p *= 0.5
        if "QUOTABLE" in (gm.get("traits") or []):
            p *= 1.5
        p *= STAGE_W[stage]
        if rng.random() >= p:
            continue
        assets = turn.get("assets") or []
        named = [a["player_id"] for a in assets if a.get("type") == "PLAYER" and a.get("to") == tid]
        named = named if gm.get("aggressiveness", 0.5) >= 0.65 else []
        positions = []
        for a in assets:
            if a.get("type") == "PLAYER":
                pobj = books.get("players", {}).get(a.get("player_id"), {})
                positions.append({"pos": pobj.get("pos"), "from": _abbr(books, a.get("from")),
                                  "to": _abbr(books, a.get("to"))})
        picks = sum(1 for a in assets if a.get("type") == "PICK")
        insiders = _insider_keys()
        insider = None
        if insiders:
            rel = (books.get("insider_relationships") or {}).get(tid) or set()
            weights = [2.0 if k in rel else 1.0 for k in insiders]
            insider = rng.choices(insiders, weights=weights)[0]
        detail = {"teams": [_abbr(books, initiator), _abbr(books, other)], "initiator": initiator,
                  "positions": positions, "picks": picks}
        return [Leak(negotiation_id=talk["id"], turn_no=turn.get("turn_no") or 0, season=talk["season"],
                     week=books.get("week") or talk.get("week"), leaker_team_id=tid, stage=stage,
                     named_player_ids=named, detail=detail, insider_key=insider)]
    return []


# --------------------------------------------------------------- read ----
def _public_assets(cur, assets, info):
    out = []
    for a in assets or []:
        if a.get("type") == "PLAYER":
            nm, pos, ovr = info.get(a.get("player_id"), (str(a.get("player_id")), None, None))
            label = f"{pos or '?'} {nm} {ovr or ''}".strip()
        else:
            label = f"{a.get('draft_season')} R{a.get('round')} ({a.get('original_team')})"
        out.append({"from": a.get("from"), "to": a.get("to"), "label": label})
    return out


def talk_json(cur, nid, public=True):
    """The transcript: §2.3's public shape (no su, sg, options, choice_source or legality) or the full one."""
    n = _talk(cur, nid)
    if not n:
        return None
    initiator, other = _initiator(n), n["gm_team_id"]
    cur.execute("SELECT id, abbreviation FROM teams WHERE id = ANY(%s)", ([initiator, other],))
    abbrs = {r[0]: r[1] for r in cur.fetchall()}
    cur.execute("SELECT team_id, gm_name FROM gm_profiles WHERE team_id = ANY(%s) AND end_season IS NULL",
                ([initiator, other],))
    gms = {r[0]: r[1] for r in cur.fetchall()}
    turns = _turns(cur, nid)
    pids = sorted({a["player_id"] for t in turns for a in (t.get("assets") or []) if a.get("type") == "PLAYER"})
    info = {}
    if pids:
        cur.execute("""SELECT p.id, COALESCE(p.full_name, p.player_name), ps.position, ps.overall_rating
                       FROM players p LEFT JOIN player_seasons ps ON ps.player_id = p.id
                        AND ps.season = (SELECT current_season FROM league_state WHERE id = 1)
                       WHERE p.id = ANY(%s)""", (pids,))
        info = {r[0]: r[1:] for r in cur.fetchall()}
    out_turns = []
    for t in turns:
        team = abbrs.get(_turn_actor_team(t, n))
        row = {"turn_no": t["turn_no"], "week": n["week"], "team": team, "action": t["action"],
               "posture": t.get("posture"), "assets": _public_assets(cur, t.get("assets"), info),
               "message": t.get("message")}
        if not public:
            row.update(su=t.get("surplus_user"), sg=t.get("surplus_gm"), options=t.get("options"),
                       choice=t.get("choice"), choice_source=t.get("choice_source"),
                       legality=t.get("legality"))
        out_turns.append(row)
    final_assets = turns[-1].get("assets") if turns else None
    out = {"id": nid, "kind": n.get("kind") or "USER", "season": n["season"],
           "weeks": list(range(n["week"], n["week"] + (n.get("carried_weeks") or 0) + 1)),
           "status": n["status"], "proposal_id": n.get("proposal_id"),
           "teams": [{"abbr": abbrs.get(initiator), "gm": gms.get(initiator), "role": "initiator"},
                     {"abbr": abbrs.get(other), "gm": gms.get(other)}],
           "turns": out_turns, "final": {"assets": _public_assets(cur, final_assets, info)}}
    if not public:
        out["teams"][0]["team_id"] = initiator
        out["teams"][1]["team_id"] = other
        out["published_at"] = n.get("published_at")
        out["source_proposal_id"] = n.get("source_proposal_id")
    return out


def talk_thread(cur, nid):
    """Read-only inbox adapter (V2.9's Message shape, no ids): the initiator's side is JR's, the other GM's the
    counterpart. For a later inbox or site view of a CPU talk."""
    n = _talk(cur, nid)
    if not n:
        return []
    initiator = _initiator(n)
    out = []
    for t in _turns(cur, nid):
        actor = _turn_actor_team(t, n)
        out.append({"author": "JR" if actor == initiator else "COUNTERPART",
                    "body": t.get("message") or "", "source": t.get("message_source") or "ENGINE",
                    "engine_ref": {"negotiation_id": nid, "turn_no": t["turn_no"]},
                    "created_at": t.get("created_at")})
    return out


def publish_talk(cur, nid):
    """Publish a completed CPU deal's transcript (idempotent). Returns rows changed."""
    cur.execute("""UPDATE gm_trade_negotiations SET published_at = COALESCE(published_at, now()), updated_at = now()
                   WHERE id = %s AND status = 'AGREED'""", (nid,))
    return cur.rowcount


# --------------------------------------------------------------- pending move ----
def queue_trade_move(cur, proposal_id):
    """On an agreed JR deal insert the TRADE pending move the league office writes (§2.4). No-op when
    franchise_pending_moves doesn't exist (older saves); idempotent per proposal."""
    cur.execute("SELECT to_regclass('franchise_pending_moves') IS NOT NULL")
    if not cur.fetchone()[0]:
        return None
    cur.execute("SELECT team_a_id, team_b_id FROM gm_trade_proposals WHERE id = %s", (proposal_id,))
    row = cur.fetchone()
    if not row:
        return None
    user, other = row
    cur.execute("""SELECT 1 FROM franchise_pending_moves
                   WHERE kind = 'TRADE' AND team_id = %s AND payload->>'proposal_id' = %s""",
                (user, str(proposal_id)))
    if cur.fetchone():
        return None
    cur.execute("""SELECT asset_type, player_id, pick_draft_season, pick_round, pick_original_team_id,
                          from_team_id, to_team_id
                   FROM gm_trade_assets WHERE proposal_id = %s ORDER BY id""", (proposal_id,))
    send = {"players": [], "picks": []}
    receive = {"players": [], "picks": []}
    for atype, pid, ds, rd, orig, frm, to in cur.fetchall():
        side = send if frm == user else receive
        if atype == "PLAYER":
            side["players"].append(pid)
        else:
            side["picks"].append({"season": ds, "round": rd, "original_team_id": orig})
    payload = {"proposal_id": proposal_id, "other_team_id": other, "send": send, "receive": receive}
    cur.execute("INSERT INTO franchise_pending_moves (team_id, kind, payload) VALUES (%s, 'TRADE', %s) "
                "RETURNING id", (user, json.dumps(payload)))
    return cur.fetchone()[0]


# --------------------------------------------------------------- CLI ----
def _dsn():
    import os
    return os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")


def replay_target(league, season=None, week=None):
    """(season, week, window) for --replay-week. An explicit --week replays in that week's own window; without one,
    the league's current season/week/window. A past week must not inherit the league's current window: the 2036
    archive sits at week 21, past the deadline, so `--week 6` used to find the window closed and replay nothing."""
    current_season, current_week, current_window = league
    if week is None:
        return season or current_season, current_week, current_window
    return season or current_season, week, _ge().window_for(week)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--show", type=int, metavar="ID", help="print a talk's transcript")
    g.add_argument("--replay-week", action="store_true", help="resume this season's open CPU talks")
    ap.add_argument("--season", type=int)
    ap.add_argument("--week", type=int)
    ap.add_argument("--dry-run", action="store_true", help="read-only: roll back")
    ap.add_argument("--no-llm", action="store_true", help="policy and templates only")
    a = ap.parse_args(argv)
    import psycopg
    from engines import trade_value as tv
    from engines.gm_llm import flush, make_llm
    with psycopg.connect(_dsn()) as conn:
        cur = conn.cursor()
        if a.show is not None:
            print(json.dumps(talk_json(cur, a.show, public=True), indent=1, default=str))
            return
        season, week, window = replay_target(_ge().league_window(cur), a.season, a.week)
        if window is None:
            print(f"trade window closed (week {week})")
            return
        b = _ge().read_books(cur, season, week, window)
        valuer = _ge().Valuer(b, tv.fits_from_db(cur, season))
        llm = None if (a.no_llm or a.dry_run) else make_llm("gm_talks")
        rng = random.Random(f"talks:{season}:{week}:{window}:replay")
        rep = resume_talks(cur, b, valuer, rng, llm, log=print)
        print(f"resumed {len(rep['talks'])} talks: {len(rep['agreed'])} agreed, {len(rep['stalled'])} stalled")
        if a.dry_run:
            conn.rollback()
            print("dry run: rolled back")
        flush(llm)


if __name__ == "__main__":
    main()
