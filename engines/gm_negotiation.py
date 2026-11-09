"""
Trade negotiations with the GM agents (PROJECT_SPEC §2.17 Negotiation, §3.3f; #debug Negotiate card).

JR, running one of his user-controlled teams, offers a package to another team's GM. The GM judges it with the
engine's own numbers (gm_trade_engine: each side's surplus, the legality checks, the acceptance threshold) and
answers ACCEPT, COUNTER, REJECT or WALK_AWAY:
  - floor = θ_T (gm_trade_engine.threshold, with the urgent-need overpay) + a hidden reserve seeded per
    negotiation (up to RESERVE_MAX x (1 - aggressiveness)). A legal offer whose GM surplus clears the floor is
    accepted. Fairness (the neutral-value gap) is reported, never a block: JR may overpay.
  - Otherwise the GM counters: the smallest edits of JR's offer (add one of JR's free assets, drop one of JR's
    assets, drop a non-core asset the GM sends, swap one of JR's assets for another; up to two edits) that are
    legal and give the GM floor + markup, the markup (MARKUP_SHARE of what the GM sends, at least MARKUP_FLOOR,
    x (1 - aggressiveness / 2)) halving with each counter he has made; among those the one best for JR. The
    core (the asset JR wants most, by league value) never comes off. No counter reaches the floor -> REJECT.
  - Patience: PATIENCE_MIN + round(PATIENCE_SPAN x aggressiveness) rounds; each declined offer costs one, an
    insulting one (GM surplus below the floor by more than max(INSULT_FLOOR, INSULT_SHARE x what he sends))
    two. At zero he walks away and won't reopen with that team until the window moves (next week or offseason
    stage).
JR can accept the GM's last counter (re-judged against fresh books first), make another offer, or close the
talks. An agreed deal is stored as an APPROVED USER proposal (flag negotiated:<id>, rationale in both GMs'
voices) with the Madden steps; JR makes it in Madden and clicks Made it. The GM's replies are written in his
voice through the shared line writer (engines.gm_writer.write_line: his voice card, the v2 fact sheet, this
turn's fixed move and the scene), with the old template as its fallback (GM_TALK_VOICE_PLAN §2.6). An OPEN
negotiation expires when the league's week, window or offseason stage moves on.
"""
import itertools
import json
import random

from engines import gm_trade_engine as ge
from engines.front_office import FrontOfficeError, _json_asset, _team, _team_ids, to_engine_assets

PATIENCE_MIN, PATIENCE_SPAN = 2, 3
RESERVE_MAX = 750_000
INSULT_SHARE, INSULT_FLOOR = 0.5, 3_000_000
MARKUP_SHARE, MARKUP_FLOOR, MARKUP_DECAY = 0.15, 500_000, 0.5
EDIT_COST = 250_000                      # per edit away from JR's offer, when choosing the counter
POOL_PLAYERS = 8                         # JR's non-starters the GM may ask for (best by the GM's value)
TOP_SINGLES = 15                         # single edits combined into two-edit counters
OPTION_K = 3                             # counters `counter_options` offers a chooser (gm_talks.OPTION_K)


# ------------------------------------------------------------------- pure ----
def gm_floor(b, deal, gm, reserve):
    ctx = b["ctx"][gm]
    urgent = ctx.get("urgent_need") and any(
        x["type"] == "PLAYER" and x["to"] == gm and b["players"][x["player_id"]]["group"] == ctx["urgent_need"].get("group")
        for x in deal["assets"])
    return ge.threshold(ctx, bool(urgent), b.get("window", "IN_SEASON")) + reserve


def gm_sends(deal, valuer, gm):
    return sum(max(0, valuer.league(x)) for x in deal["assets"] if x["from"] == gm)


def surpluses(deal, valuer, user):
    """(JR's surplus, the GM's surplus)."""
    da, db, _, _ = ge.deltas(deal, valuer)
    return (da, db) if deal["a"] == user else (db, da)


def judge(b, valuer, deal, user, gm, reserve):
    """The GM's read of a package: {legal, checks, su, sg, floor, mood, ok}. mood: CLOSE | APART | INSULTED (None
    when it clears the floor). checks leave fairness out (reported separately)."""
    checks = ge.check_legality(deal, b, None)
    su, sg = surpluses(deal, valuer, user)
    fl = gm_floor(b, deal, gm, reserve)
    insult = max(INSULT_FLOOR, INSULT_SHARE * gm_sends(deal, valuer, gm))
    lg = ge.legal(checks)
    mood = None if sg >= fl else ("INSULTED" if sg < fl - insult else "CLOSE" if sg >= fl - insult / 3 else "APART")
    return {"legal": lg, "checks": checks, "su": su, "sg": sg, "floor": fl, "mood": mood, "ok": lg and sg >= fl}


def markup(b, valuer, deal, gm, counters_made):
    agg = b["ctx"][gm]["aggressiveness"]
    m = max(MARKUP_FLOOR, MARKUP_SHARE * gm_sends(deal, valuer, gm)) * (1 - agg / 2)
    return m * MARKUP_DECAY ** counters_made


def core_asset(deal, valuer, gm):
    """What JR wants most: the GM-side asset with the highest league value (None when the GM sends nothing)."""
    mine = [x for x in deal["assets"] if x["from"] == gm]
    return max(mine, key=lambda x: valuer.league(x)) if mine else None


def user_pool(b, valuer, deal, user, gm):
    """JR's free assets not already in the deal, as legs to the GM: his best POOL_PLAYERS non-starters by the GM's
    value and every owned free pick."""
    keys = {ge.asset_key(x) for x in deal["assets"]}
    pool = [dict(x, to=gm) for x in ge.package_assets(b, user) if ge.asset_key(x) not in keys]
    players = sorted((x for x in pool if x["type"] == "PLAYER"), key=lambda x: -valuer.value(x, gm))[:POOL_PLAYERS]
    return players + [x for x in pool if x["type"] == "PICK"]


def edits(deal, valuer, user, gm, pool):
    """Every single edit of a deal: [(label, assets)]."""
    core = core_asset(deal, valuer, gm)
    ck = ge.asset_key(core) if core else None
    mine = [x for x in deal["assets"] if x["from"] == user]
    theirs = [x for x in deal["assets"] if x["from"] == gm and ge.asset_key(x) != ck]
    out = []
    for p in pool:
        out.append((("add", ge.asset_key(p)), deal["assets"] + [p]))
    for x in mine + theirs:
        out.append((("drop", ge.asset_key(x)), [y for y in deal["assets"] if y is not x]))
    for x in mine:
        for p in pool:
            out.append((("swap", ge.asset_key(x), ge.asset_key(p)), [p if y is x else y for y in deal["assets"]]))
    return out


def counter_options(b, valuer, deal, user, gm, reserve, counters_made, k=OPTION_K):
    """Up to k distinct legal counters, best for JR first: legal, the GM's surplus at least floor + markup (else at
    least the floor), ordered by JR's surplus minus EDIT_COST per edit. `counter_options(...)[0]` is exactly the old
    single `counter` result; the dearest (last) is what a HOLD posture offers. Returns [{assets, su, sg, edits,
    deal, checks}]."""
    pool = user_pool(b, valuer, deal, user, gm)
    seen = {ge.deal_hash(user, gm, deal["assets"])}
    cands = []

    def consider(assets, n):
        if not any(x["from"] == user for x in assets) and not any(x["from"] == gm for x in assets):
            return
        h = ge.deal_hash(user, gm, assets)
        if h in seen:
            return
        seen.add(h)
        d = {"a": user, "b": gm, "assets": assets}
        su, sg = surpluses(d, valuer, user)
        cands.append({"assets": assets, "su": su, "sg": sg, "edits": n, "deal": d})

    singles = edits(deal, valuer, user, gm, pool)
    for _, assets in singles:
        consider(assets, 1)
    best = sorted(cands, key=lambda c: -c["sg"])[:TOP_SINGLES]
    for c1, c2 in itertools.combinations(best, 2):
        merged = _merge(deal["assets"], c1["assets"], c2["assets"])
        if merged is not None:
            consider(merged, 2)
    fl = gm_floor(b, deal, gm, reserve)
    target = fl + markup(b, valuer, deal, gm, counters_made)
    out, chosen = [], set()
    for bar in (target, fl):
        if out:
            break
        for c in sorted((c for c in cands if c["sg"] >= bar), key=lambda c: -(c["su"] - EDIT_COST * c["edits"])):
            checks = ge.check_legality(c["deal"], b, None)
            if not ge.legal(checks) or c["sg"] < gm_floor(b, c["deal"], gm, reserve):
                continue
            h = ge.deal_hash(c["deal"]["a"], c["deal"]["b"], c["assets"])
            if h in chosen:
                continue
            chosen.add(h)
            out.append(dict(c, checks=checks))
            if len(out) >= k:
                break
    return out


def counter(b, valuer, deal, user, gm, reserve, counters_made):
    """The GM's counter to JR's deal, or None: the best of `counter_options` (behaviour unchanged)."""
    opts = counter_options(b, valuer, deal, user, gm, reserve, counters_made)
    return opts[0] if opts else None


def _merge(base, a1, a2):
    """Two single edits of `base` applied together (None when they touch the same asset)."""
    k = lambda xs: {ge.asset_key(x): x for x in xs}
    kb, k1, k2 = k(base), k(a1), k(a2)
    d1 = (set(kb) - set(k1), set(k1) - set(kb))
    d2 = (set(kb) - set(k2), set(k2) - set(kb))
    if (d1[0] | d1[1]) & (d2[0] | d2[1]):
        return None
    keep = {key: x for key, x in kb.items() if key not in d1[0] | d2[0]}
    keep.update({key: k1[key] for key in d1[1]})
    keep.update({key: k2[key] for key in d2[1]})
    return list(keep.values())


def respond(b, valuer, deal, user, gm, reserve, patience_left, counters_made):
    """The GM's answer to JR's offer: {action ACCEPT | COUNTER | REJECT | WALK_AWAY, judge, patience_left,
    counter?, reasons}. Pure."""
    j = judge(b, valuer, deal, user, gm, reserve)
    reasons = [f"{k}: {c['detail']}" for k, c in j["checks"].items() if not c["ok"]]
    if j["ok"]:
        return {"action": "ACCEPT", "judge": j, "patience_left": patience_left, "reasons": []}
    left = patience_left - (2 if j["mood"] == "INSULTED" else 1)
    if left <= 0:
        return {"action": "WALK_AWAY", "judge": j, "patience_left": 0, "reasons": reasons}
    c = counter(b, valuer, deal, user, gm, reserve, counters_made)
    if c is None:
        if not reasons:
            reasons = ["no version of this deal works for us"]
        return {"action": "REJECT", "judge": j, "patience_left": left, "reasons": reasons}
    return {"action": "COUNTER", "judge": j, "patience_left": left, "counter": c, "reasons": reasons}


def diff(b, before, after):
    """What a counter changed: {added: [labels], removed: [labels]} (assets as legs)."""
    kb = {ge.asset_key(x): x for x in before}
    ka = {ge.asset_key(x): x for x in after}
    return {"added": [ge.asset_label(b, ka[k]) for k in ka if k not in kb],
            "removed": [ge.asset_label(b, kb[k]) for k in kb if k not in ka]}


# --------------------------------------------------------------- messages ----
MOOD_LINES = {"INSULTED": "That's not a serious offer.", "APART": "We're a ways apart.", "CLOSE": "We're close.",
              None: ""}


def template_message(b, kind, user, gm, offer, counter_assets=None, mood=None, reasons=()):
    gm_info = b["gm"].get(gm, {})
    name = gm_info.get("gm_name") or ge.team_name(b, gm)
    traits = set(gm_info.get("traits") or [])
    agg = gm_info.get("aggressiveness", 0.5)
    gets = lambda assets, t: ", ".join(ge.asset_label(b, x) for x in assets if x["to"] == t) or "nothing"

    if kind == "ACCEPT":
        if "LOYAL" in traits:
            return f"{name}: We can do that. Good doing business with you — we take care of our guys and this fits what we're building."
        if agg >= 0.65:
            return f"{name}: Deal. Send the paperwork over and let's get it filed right away."
        if agg <= 0.35:
            return f"{name}: We've weighed this carefully. We can do that. Send it over and we'll get it through on our end."
        return f"{name}: We can do that. Send it over and we'll get it through on our end."

    if kind == "WALK_AWAY":
        if "GRUDGE_HOLDER" in traits or agg >= 0.65:
            return f"{name}: We've gone back and forth enough and we're not getting anywhere. I'm moving on."
        if agg <= 0.35:
            return f"{name}: We've been patient here, but we've gone back and forth enough. Let's pick this up another time."
        return f"{name}: We've gone back and forth enough. Let's pick this up another time."

    if kind == "REJECT":
        why = f" ({'; '.join(reasons)})" if reasons else ""
        if "LOYAL" in traits and any(x["from"] == gm for x in offer):
            return f"{name}: {MOOD_LINES.get(mood) + ' ' if MOOD_LINES.get(mood) else ''}We're loyal to our guys and I can't find a version of this that works for us{why}."
        if agg >= 0.65:
            return f"{name}: {MOOD_LINES.get(mood) + ' ' if MOOD_LINES.get(mood) else ''}That doesn't move the needle for us. I can't find a version of this that works{why}."
        return f"{name}: {MOOD_LINES.get(mood) + ' ' if MOOD_LINES.get(mood) else ''}I can't find a version of this " \
               f"that works for us{why}."

    d = diff(b, offer, counter_assets)
    change = "; ".join(filter(None, [f"add {', '.join(d['added'])}" if d["added"] else "",
                                     f"take off {', '.join(d['removed'])}" if d["removed"] else ""]))
    if "CAP_MIND" in traits:
        prefix = "From an asset and value standpoint, here's where we'd get it done"
    elif "OLD_SCHOOL" in traits:
        prefix = "We need real pieces to make this work. Here's where we'd get it done"
    elif agg >= 0.65:
        prefix = "Here's our bottom line to get this done today"
    elif agg <= 0.35:
        prefix = "We're willing to work with you on this. Here's where we'd get it done"
    else:
        prefix = "Here's where we'd get it done"

    return (f"{name}: {MOOD_LINES.get(mood) + ' ' if MOOD_LINES.get(mood) else ''}{prefix}"
            f"{f' ({change})' if change else ''}: you get {gets(counter_assets, user)}, we get "
            f"{gets(counter_assets, gm)}.")


def message_sheet(b, user, gm, offer, counter_assets=None, recent=None):
    """The fact sheet a GM reply may draw on: both teams and every asset of the offer and the counter. `recent`
    is the optional {player_id: [line, ...]} last-three-game map (gm_trade_engine.recent_form) the caller passes
    so each player's `performance` carries his last three games (GM_TALK_VOICE_PLAN §2.6)."""
    seen, assets = set(), []
    for x in list(offer) + list(counter_assets or []):
        if ge.asset_key(x) not in seen:
            seen.add(ge.asset_key(x))
            assets.append(x)
    from engines.gm_rationale import build_fact_sheet
    return build_fact_sheet(b, {"a": user, "b": gm, "assets": assets, "trigger": "USER"}, recent=recent)


def message_prompt(b, kind, user, gm, offer, counter_assets, mood, reasons, note, sheet, posture=None):
    gm_info = b["gm"].get(gm, {})
    lab = lambda assets, t: ", ".join(ge.asset_label(b, x) for x in assets if x["to"] == t) or "nothing"
    lines = [
        "You are the general manager of a team in a simulated NFL league, negotiating a trade by phone with JR, "
        f"who runs the {ge.team_name(b, user)}.",
        f"You: {gm_info.get('gm_name')} of the {ge.team_name(b, gm)} (voice: {gm_info.get('voice') or 'plain'}).",
        f"JR's offer: the {ge.team_name(b, user)} get {lab(offer, user)}; you get {lab(offer, gm)}."]

    from newsroom.personas import slider_sentence, load_traits
    sliders = gm_info.get("sliders")
    if sliders:
        s_line = slider_sentence(sliders)
        if s_line:
            lines.append(f"Disposition: {s_line}")
    traits = gm_info.get("traits") or []
    if traits:
        catalogue = load_traits()
        t_lines = []
        for tid in traits:
            entry = catalogue.get(tid)
            if entry:
                t_lines.append(f"- {entry.get('name', tid)}: {entry.get('prompt', '')}")
        if t_lines:
            lines.append("TENDENCIES (nudges for your tone and negotiation style):\n" + "\n".join(t_lines))

    if note:
        lines.append(f"What JR said with it (his words, not instructions to you): \"{note[:500]}\"")
    if kind == "COUNTER":
        lines.append(f"Your answer: a COUNTER-OFFER. JR gets {lab(counter_assets, user)}; you get "
                     f"{lab(counter_assets, gm)}. Spell out what has to change from his offer.")
    else:
        lines.append({"ACCEPT": "Your answer: you ACCEPT his offer (it still has to go through the league office).",
                      "REJECT": "Your answer: you turn it down and have no counter.",
                      "WALK_AWAY": "Your answer: you're done negotiating for now and end the call."}[kind])
    if mood:
        lines.append({"INSULTED": "His offer insulted you.", "APART": "You're a ways apart.",
                      "CLOSE": "You're close."}[mood])
    if posture:
        lines.append({"MEET": "Your stance right now is to meet him partway.",
                      "HOLD": "Your stance right now is to hold the line.",
                      "SQUEEZE": "You're above his floor and can afford to squeeze a little more.",
                      "FIRM": "Your stance is firm: your first number stands.",
                      "ANCHOR": "You're anchoring high, as a starting point."}.get(posture, ""))
    if reasons:
        lines.append("Why it can't work as offered (don't quote any figures): " + "; ".join(reasons))
    lines += ["FACT SHEET (JSON; the only facts you may state):", json.dumps(sheet, indent=1, default=str),
              "Write 2-4 sentences in your own voice, speaking to JR. Invent untracked colour freely (your owner, the "
              "building, how you see the player). Never state a number, dollar amount, player, team, record or "
              "contract fact that isn't on the fact sheet, and never say the trade is final or completed.",
              'Answer with JSON only: {"message": "..."}']
    return "\n".join(lines)


MOVE_ACTIONS = ("OFFER", "ACCEPT", "COUNTER", "REJECT", "WALK_AWAY")
_MOOD_SCENE = {"INSULTED": "insulted", "APART": "a ways apart", "CLOSE": "close"}


def _labels_to(b, assets, team):
    return [ge.asset_label(b, x) for x in assets if x.get("to") == team]


def message_move(b, kind, user, gm, offer, counter_assets=None, posture=None):
    """The writer's fixed move for this turn: the action the engine already decided, the GM's posture and both
    sides' asset labels (his side `gets` what goes to him, `gives` what goes to JR)."""
    assets = (counter_assets or []) if kind == "COUNTER" else list(offer or [])
    return {"action": kind if kind in MOVE_ACTIONS else "COUNTER", "posture": posture,
            "gets": _labels_to(b, assets, gm), "gives": _labels_to(b, assets, user)}


def _ordinal(n):
    try:
        n = int(n)
    except (TypeError, ValueError):
        return str(n)
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _stakes_text(stakes):
    """The GM team's stakes as one plain string ("7-3, won 3 straight, 2nd in the conference"), or None."""
    stakes = stakes or {}
    bits = []
    if stakes.get("record"):
        bits.append(str(stakes["record"]))
    streak = stakes.get("streak")
    if streak:
        word = "won" if str(streak).upper().startswith("W") else "lost"
        bits.append(f"{word} {str(streak)[1:]} straight")
    if stakes.get("seed"):
        bits.append(f"{_ordinal(stakes['seed'])} in the conference")
    return ", ".join(bits) or None


def _relationship_text(rivalry):
    return {"division": "division rivals", "conference": "conference rivals"}.get((rivalry or {}).get("relation"))


def message_scene(b, gm, sheet, mood=None):
    """The writer's scene for this turn: the week, the trade deadline, who called, the mood, the GM team's stakes
    and the two clubs' relationship — all plain strings from the books and the sheet (GM_TALK_VOICE_PLAN §2.6)."""
    side = sheet["teams"]["b"]                # message_sheet builds the deal as {a: user, b: gm}
    return {"week": b.get("week"), "deadline_week": ge.TRADE_DEADLINE_WEEK, "who_called": "JR",
            "mood": _MOOD_SCENE.get(mood), "stakes": _stakes_text(side.get("stakes")),
            "relationship": _relationship_text(side.get("rivalry")), "speaker": side.get("abbr")}


def _gm_card_text(b, gm):
    """The GM's rendered voice card: his stored `voice_card` when it parses, else the trait template built from
    the same dict, so he is never voiceless. The writer only ever receives this rendered text."""
    from engines import gm_voices
    info = b["gm"].get(gm) or {}
    card = gm_voices.parse_card(info.get("voice_card")) if info.get("voice_card") else None
    if not card:
        card = gm_voices.template_card({k: info.get(k) for k in
                                        ("aggressiveness", "risk_appetite", "pick_affinity", "youth_bias")})
    return gm_voices.render_card(card)


def gm_message(b, kind, user, gm, offer, counter_assets=None, mood=None, reasons=(), note=None, llm=None,
               league=None, timeout=None, posture=None, recent=None):
    """(text, source): the shared writer's line (gm_writer.write_line) from the GM's voice card, the v2 fact
    sheet and this turn's move and scene, else the template. The engine has already decided `kind`, `posture` and
    the assets, and the writer never changes them. `recent` is the {player_id: [line, ...]} last-three-game map
    from gm_trade_engine.recent_form. JR's `note`, when present, rides in the writer's `history` as the line he
    just said, so the GM answers it; `league` stays for old callers (the writer has no name check). timeout: the
    reply-time budget (gm_talks.call_with_timeout), unchanged."""
    fallback = template_message(b, kind, user, gm, offer, counter_assets, mood, reasons)
    if llm is None:
        return fallback, "TEMPLATE"
    from engines.gm_writer import write_line
    sheet = message_sheet(b, user, gm, offer, counter_assets, recent=recent)
    history = [{"message": f"JR: {note[:500]}", "source": "USER"}] if note else []
    return write_line(llm, card_text=_gm_card_text(b, gm), sheet=sheet, history=history,
                      move=message_move(b, kind, user, gm, offer, counter_assets, posture),
                      scene=message_scene(b, gm, sheet, mood), fallback=fallback, timeout=timeout)


# --------------------------------------------------------------------- DB ----
def installed(cur):
    cur.execute("SELECT to_regclass('gm_trade_negotiations') IS NOT NULL")
    return bool(cur.fetchone()[0])


def _column_exists(cur, table, column):
    cur.execute("SELECT 1 FROM information_schema.columns WHERE table_name = %s AND column_name = %s",
                (table, column))
    return cur.fetchone() is not None


def _books(cur):
    from engines import trade_value as tv
    season, week, window = ge.league_window(cur)
    if window not in ("IN_SEASON", "OFFSEASON"):
        raise FrontOfficeError(409, f"the trade window is closed (week {week}; the deadline is week "
                                    f"{ge.TRADE_DEADLINE_WEEK})")
    b = ge.read_books(cur, season, week, window)
    return b, ge.Valuer(b, tv.fits_from_db(cur, season))


def reserve_for(season, nid, aggressiveness):
    return round(random.Random(f"neg:{season}:{nid}").random() * RESERVE_MAX * (1 - aggressiveness))


def patience_for(aggressiveness):
    return PATIENCE_MIN + round(PATIENCE_SPAN * aggressiveness)


COLS = ("id", "season", "week", "trade_window", "offseason_stage", "user_team_id", "gm_team_id", "status",
        "status_reason", "patience", "patience_left", "proposal_id", "created_at", "updated_at")


def _neg(cur, nid):
    cur.execute(f"SELECT {', '.join(COLS)} FROM gm_trade_negotiations WHERE id = %s", (nid,))
    r = cur.fetchone()
    if not r:
        raise FrontOfficeError(404, f"no negotiation {nid}")
    return dict(zip(COLS, r))


def _turns(cur, nid):
    cur.execute("""SELECT turn_no, actor, action, assets, surplus_user, surplus_gm, mood, legality, message,
                          message_source, actor_team_id, options, choice, choice_source, posture, created_at
                   FROM gm_trade_negotiation_turns WHERE negotiation_id = %s ORDER BY turn_no""", (nid,))
    cols = ("turn_no", "actor", "action", "assets", "surplus_user", "surplus_gm", "mood", "legality", "message",
            "message_source", "actor_team_id", "options", "choice", "choice_source", "posture", "created_at")
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _add_turn(cur, nid, actor, action, assets=None, su=None, sg=None, mood=None, legality=None, message=None,
              source=None, abbr=None, actor_team_id=None, options=None, choice=None, choice_source=None,
              posture=None):
    cur.execute("SELECT COALESCE(max(turn_no), 0) + 1 FROM gm_trade_negotiation_turns WHERE negotiation_id = %s",
                (nid,))
    n = cur.fetchone()[0]
    js = None if assets is None else json.dumps([_json_asset(x, abbr) for x in assets])
    cur.execute("""INSERT INTO gm_trade_negotiation_turns (negotiation_id, turn_no, actor, action, actor_team_id,
                       assets, options, choice, choice_source, posture, surplus_user, surplus_gm, mood, legality,
                       message, message_source)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (nid, n, actor, action, actor_team_id, js,
                 json.dumps(options, default=str) if options is not None else None, choice, choice_source, posture,
                 su, sg, mood, json.dumps(legality) if legality else None, message, source))
    cur.execute("UPDATE gm_trade_negotiations SET updated_at = now() WHERE id = %s", (nid,))
    return n


def _set_status(cur, nid, status, reason=None, patience_left=None, proposal_id=None):
    cur.execute("""UPDATE gm_trade_negotiations SET status = %s, status_reason = COALESCE(%s, status_reason),
                       patience_left = COALESCE(%s, patience_left), proposal_id = COALESCE(%s, proposal_id),
                       updated_at = now() WHERE id = %s""", (status, reason, patience_left, proposal_id, nid))


def _window_key(cur):
    season, week, window = ge.league_window(cur)
    stage = ge.read_stage(cur, season) if window == "OFFSEASON" else None
    return season, week, window, stage


def expire_stale(cur):
    """OPEN negotiations from another week, window or offseason stage -> EXPIRED. Returns how many. CPU talks are
    owned by engines.gm_talks (their loop resumes or stalls them), so they are left alone; the filter only applies
    on a database that has the 2027_01_22 migration."""
    season, week, window, stage = _window_key(cur)
    skip_cpu = " AND COALESCE(kind, 'USER') <> 'CPU'" if _column_exists(
        cur, "gm_trade_negotiations", "kind") else ""
    cur.execute(f"""UPDATE gm_trade_negotiations SET status = 'EXPIRED', status_reason = 'the window moved on',
                        updated_at = now()
                    WHERE status = 'OPEN'{skip_cpu} AND (season <> %s OR week <> %s OR trade_window IS DISTINCT FROM %s
                                               OR offseason_stage IS DISTINCT FROM %s)""",
                (season, week, window, stage))
    return cur.rowcount


def _engine_assets(cur, b, user, gm, assets):
    eng = to_engine_assets(cur, assets)
    if not eng:
        raise FrontOfficeError(400, "an offer needs at least one asset")
    for x in eng:
        if {x["from"], x["to"]} != {user, gm}:
            raise FrontOfficeError(400, "every leg must go between your team and the GM's team")
        if x["type"] == "PLAYER" and x["player_id"] not in b["players"]:
            raise FrontOfficeError(400, f"player {x['player_id']} isn't on a roster this season")
    if len({ge.asset_key(x) for x in eng}) != len(eng):
        raise FrontOfficeError(400, "an asset appears twice")
    return eng


def _abbr(b):
    return {t: i["abbr"] for t, i in b["teams"].items()}


def open_negotiation(cur, user_team, gm_team, assets, note=None, llm=None, timeout=None, posture=None):
    """JR opens talks with a GM: his first offer and the GM's answer. user_team must be user-controlled, the GM's
    team must not be; one OPEN negotiation per pair; a GM who walked away won't reopen until the window moves.
    timeout: the reply-time budget; posture: force the GM's counter stance (MEET or HOLD) instead of the policy's."""
    expire_stale(cur)
    ids = _team_ids(cur)
    user, gm = _team(ids, user_team), _team(ids, gm_team)
    if user == gm:
        raise FrontOfficeError(400, "pick two different teams")
    b, valuer = _books(cur)
    if not (b["gm"].get(user) or {}).get("user_controlled"):
        raise FrontOfficeError(400, f"you don't control {ge.abbr(b, user)} (tick JR controls on the Front Office card)")
    g = b["gm"].get(gm)
    if not g:
        raise FrontOfficeError(400, f"{ge.abbr(b, gm)} has no GM")
    if g["user_controlled"]:
        raise FrontOfficeError(400, f"you control {ge.abbr(b, gm)}: there's no GM to negotiate with")
    stage = ge.read_stage(cur, b["season"]) if b["window"] == "OFFSEASON" else None
    cur.execute("""SELECT id, status FROM gm_trade_negotiations WHERE season = %s AND user_team_id = %s
                   AND gm_team_id = %s AND (status = 'OPEN' OR (status = 'WALKED_AWAY' AND week = %s
                                            AND offseason_stage IS NOT DISTINCT FROM %s))""",
                (b["season"], user, gm, b["week"], stage))
    r = cur.fetchone()
    if r and r[1] == "OPEN":
        raise FrontOfficeError(409, f"talks with {ge.abbr(b, gm)} are already open (#{r[0]})")
    if r:
        raise FrontOfficeError(409, f"{g['gm_name']} walked away this week; try again after the window moves on")
    eng = _engine_assets(cur, b, user, gm, assets)
    patience = patience_for(g["aggressiveness"])
    cur.execute("""INSERT INTO gm_trade_negotiations (season, week, trade_window, offseason_stage, user_team_id,
                       gm_team_id, patience, patience_left) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (b["season"], b["week"], b["window"], stage, user, gm, patience, patience))
    nid = cur.fetchone()[0]
    return _jr_offer(cur, b, valuer, _neg(cur, nid), eng, note, llm, timeout, posture)


def offer(cur, nid, assets, note=None, llm=None, timeout=None, posture=None):
    """JR's next offer in an OPEN negotiation."""
    expire_stale(cur)
    n = _neg(cur, nid)
    if n["status"] != "OPEN":
        raise FrontOfficeError(409, f"negotiation {nid} is {n['status']}")
    b, valuer = _books(cur)
    eng = _engine_assets(cur, b, n["user_team_id"], n["gm_team_id"], assets)
    return _jr_offer(cur, b, valuer, n, eng, note, llm, timeout, posture)


def _pick_counter_option(b, opts, gm, season, nid, turn_no, posture=None):
    """The GM's counter from `counter_options`, chosen by the persona policy (MEET = the softest, HOLD = the
    dearest) or by the caller's posture. Returns (counter, choice, options as public-free dicts)."""
    from engines.gm_talks import Option, policy_choice
    meet, hold = opts[0], opts[-1]
    options = [dict(key="MEET", action="COUNTER", posture="MEET", assets=meet["assets"], su=meet["su"],
                    sg=meet["sg"]),
               dict(key="HOLD", action="COUNTER", posture="HOLD", assets=hold["assets"], su=hold["su"],
                    sg=hold["sg"])]
    if posture in ("MEET", "HOLD"):
        choice = posture
    else:
        choice = policy_choice([Option(**o) for o in options], b["gm"].get(gm) or {},
                               random.Random(f"talk:{season}:{nid}:{turn_no}"))
    return (meet if choice == "MEET" else hold), choice, \
        [{k: o[k] for k in ("key", "action", "assets", "su", "sg")} for o in options]


def _jr_offer(cur, b, valuer, n, eng, note, llm, timeout=None, posture=None):
    from engines.gm_rationale import league_names
    nid, user, gm = n["id"], n["user_team_id"], n["gm_team_id"]
    deal = {"a": user, "b": gm, "assets": eng}
    abbr = _abbr(b)
    turns = _turns(cur, nid)
    counters_made = sum(1 for t in turns if t["action"] == "COUNTER")
    next_turn = (turns[-1]["turn_no"] + 1) if turns else 1
    reserve = reserve_for(n["season"], nid, b["gm"][gm]["aggressiveness"])
    res = respond(b, valuer, deal, user, gm, reserve, n["patience_left"], counters_made)
    j = res["judge"]
    _add_turn(cur, nid, "JR", "OFFER", eng, j["su"], j["sg"], j["mood"], j["checks"], note, "JR" if note else None,
              abbr)
    league = league_names(b) if llm is not None else None
    act = res["action"]
    c = res.get("counter")
    chosen, choice, options = None, None, None
    if act == "COUNTER" and c is not None:
        opts = counter_options(b, valuer, deal, user, gm, reserve, counters_made)
        if opts:
            c, choice, options = _pick_counter_option(b, opts, gm, n["season"], nid, next_turn, posture)
            posture = choice
    counter_assets = c["assets"] if c else None
    recent = ge.recent_form(cur, [x["player_id"] for x in list(eng) + list(counter_assets or [])
                                  if x.get("type") == "PLAYER"], b["season"], b["week"])
    text, src = gm_message(b, act, user, gm, eng, counter_assets, j["mood"], res["reasons"], note, llm,
                           league, timeout=timeout, posture=posture, recent=recent)
    if act == "COUNTER":
        _add_turn(cur, nid, "GM", "COUNTER", c["assets"], c["su"], c["sg"], None, c["checks"], text, src, abbr,
                  actor_team_id=gm, options=options, choice=choice, choice_source="POLICY" if choice else None,
                  posture=posture)
        _set_status(cur, nid, "OPEN", patience_left=res["patience_left"])
    elif act == "ACCEPT":
        _add_turn(cur, nid, "GM", "ACCEPT", eng, j["su"], j["sg"], None, j["checks"], text, src, abbr)
        pid = _agree(cur, b, valuer, nid, deal, llm)
        _set_status(cur, nid, "AGREED", "the GM accepted JR's offer", proposal_id=pid)
    elif act == "REJECT":
        _add_turn(cur, nid, "GM", "REJECT", None, None, None, j["mood"], None, text, src, abbr)
        _set_status(cur, nid, "OPEN", patience_left=res["patience_left"])
    else:
        _add_turn(cur, nid, "GM", "WALK_AWAY", None, None, None, j["mood"], None, text, src, abbr)
        _set_status(cur, nid, "WALKED_AWAY", "the GM ran out of patience", patience_left=0)
    return negotiation_json(cur, nid)


def accept_counter(cur, nid, llm=None, timeout=None, posture=None):
    """JR takes the GM's last counter. It's re-judged against fresh books (a deal that is no longer legal, or no
    longer clears the GM's floor, is refused and the talks stay open). timeout/posture are accepted for the inbox's
    uniform entry-point signature; accepting writes no reply line."""
    expire_stale(cur)
    n = _neg(cur, nid)
    if n["status"] != "OPEN":
        raise FrontOfficeError(409, f"negotiation {nid} is {n['status']}")
    turns = _turns(cur, nid)
    last = turns[-1] if turns else None
    if not last or last["action"] != "COUNTER":
        raise FrontOfficeError(409, "there's no counter-offer on the table")
    b, valuer = _books(cur)
    user, gm = n["user_team_id"], n["gm_team_id"]
    eng = _engine_assets(cur, b, user, gm, last["assets"])
    deal = {"a": user, "b": gm, "assets": eng}
    j = judge(b, valuer, deal, user, gm, reserve_for(n["season"], nid, b["gm"][gm]["aggressiveness"]))
    if not j["ok"]:
        why = "; ".join(f"{k}: {c['detail']}" for k, c in j["checks"].items() if not c["ok"]) or \
              "the GM no longer values it the same way"
        raise FrontOfficeError(409, f"the counter no longer works: {why}")
    _add_turn(cur, nid, "JR", "ACCEPT", eng, j["su"], j["sg"], None, j["checks"], None, None, _abbr(b))
    pid = _agree(cur, b, valuer, nid, deal, llm)
    _set_status(cur, nid, "AGREED", "JR accepted the GM's counter", proposal_id=pid)
    return negotiation_json(cur, nid)


def close(cur, nid, note=None):
    """JR ends the talks."""
    n = _neg(cur, nid)
    if n["status"] != "OPEN":
        raise FrontOfficeError(409, f"negotiation {nid} is {n['status']}")
    _add_turn(cur, nid, "JR", "CLOSE", message=note, source="JR" if note else None)
    _set_status(cur, nid, "CLOSED", "JR ended the talks")
    return negotiation_json(cur, nid)


def _agree(cur, b, valuer, nid, deal, llm):
    """The agreed deal -> an APPROVED USER proposal (flag negotiated:<id>) with both GMs' rationale. Returns its id."""
    checks = ge.check_legality(deal, b, valuer)
    d = dict(deal, trigger="USER", legality=checks,
             hash=f"neg{nid}:" + ge.deal_hash(deal["a"], deal["b"], deal["assets"]))
    rat = ge.rationale_for(b, d, llm)
    flags = [f"negotiated:{nid}"] + ge.release_flags(checks, b) + ge.llm_flags(rat)
    pid = ge.store(cur, b, d, valuer, rat, flags, origin="USER", status="APPROVED", trigger="USER")
    if pid is None:
        raise FrontOfficeError(409, "this deal is already stored")
    cur.execute("UPDATE gm_trade_proposals SET approved_at = now() WHERE id = %s", (pid,))
    from engines.gm_talks import queue_trade_move
    queue_trade_move(cur, pid)                 # TRADE pending move (no-op without franchise_pending_moves)
    return pid


# ------------------------------------------------------------------- read ----
def _legs_json(cur, assets):
    """Front Office JSON assets with labels (player name, position, OVR; pick label)."""
    out = []
    pids = [a["player_id"] for a in assets or [] if a.get("type") == "PLAYER"]
    info = {}
    if pids:
        cur.execute("""SELECT p.id, COALESCE(p.full_name, p.player_name), ps.position, ps.overall_rating
                       FROM players p LEFT JOIN player_seasons ps ON ps.player_id = p.id
                        AND ps.season = (SELECT current_season FROM league_state WHERE id = 1)
                       WHERE p.id = ANY(%s)""", (pids,))
        info = {r[0]: r[1:] for r in cur.fetchall()}
    for a in assets or []:
        if a.get("type") == "PLAYER":
            nm, pos, ovr = info.get(a["player_id"], (str(a["player_id"]), None, None))
            out.append(dict(a, label=f"{pos or '?'} {nm} {ovr or ''}".strip()))
        else:
            out.append(dict(a, label=f"{a['draft_season']} R{a['round']} ({a['original_team']})"))
    return out


def negotiation_json(cur, nid):
    n = _neg(cur, nid)
    cur.execute("SELECT id, abbreviation, city || ' ' || name FROM teams WHERE id = ANY(%s)",
                ([n["user_team_id"], n["gm_team_id"]],))
    t = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    cur.execute("SELECT gm_name FROM gm_profiles WHERE team_id = %s AND end_season IS NULL", (n["gm_team_id"],))
    g = cur.fetchone()
    turns = []
    for x in _turns(cur, nid):
        failed = {k: c["detail"] for k, c in (x["legality"] or {}).items() if not c.get("ok")}
        turns.append({"turn_no": x["turn_no"], "actor": x["actor"], "action": x["action"],
                      "legs": _legs_json(cur, x["assets"]) if x["assets"] else None,
                      "assets": x["assets"], "surplus_user": x["surplus_user"], "mood": x["mood"],
                      "failed_checks": failed, "message": x["message"], "message_source": x["message_source"],
                      "actor_team_id": x.get("actor_team_id"), "posture": x.get("posture"),
                      "choice": x.get("choice"), "choice_source": x.get("choice_source"),
                      "created_at": x["created_at"]})
    on_table = turns[-1] if turns and turns[-1]["action"] == "COUNTER" and n["status"] == "OPEN" else None
    return {"id": nid, "season": n["season"], "week": n["week"], "window": n["trade_window"],
            "status": n["status"], "status_reason": n["status_reason"],
            "user_team": t[n["user_team_id"]][0], "gm_team": t[n["gm_team_id"]][0],
            "user_team_name": t[n["user_team_id"]][1], "gm_team_name": t[n["gm_team_id"]][1],
            "gm_name": g[0] if g else None, "patience": n["patience"], "patience_left": n["patience_left"],
            "proposal_id": n["proposal_id"], "on_table": on_table, "turns": turns,
            "created_at": n["created_at"], "updated_at": n["updated_at"]}


def negotiations_state(cur, season=None):
    """Every negotiation of the season, newest first (stale OPEN ones expired first)."""
    expire_stale(cur)
    season = season or ge.league_window(cur)[0]
    cur.execute("SELECT id FROM gm_trade_negotiations WHERE season = %s ORDER BY updated_at DESC, id DESC",
                (season,))
    return [negotiation_json(cur, r[0]) for r in cur.fetchall()]


def team_assets(cur, user_team, gm_team):
    """The offer builder: both teams' rostered players (tradeable or why not, starter, cap hit, years left) and
    free picks, plus the GM's direction and needs."""
    ids = _team_ids(cur)
    user, gm = _team(ids, user_team), _team(ids, gm_team)
    b, _ = _books(cur)
    vs = b["value_season"]
    out = {"window": b["window"], "week": b["week"], "teams": {}}
    for t in (user, gm):
        ctx = b["ctx"][t]
        players = []
        for pid in b["roster"].get(t, []):
            p = b["players"][pid]
            ok, why = ge.tv.tradeable(p, b["injured"], b["acquired"], vs, b["window"] == "IN_SEASON")
            if ok and pid in b["booked_players"]:
                ok, why = False, "in an open proposal"
            players.append({"player_id": pid, "name": p["name"], "pos": p["pos"], "group": p["group"],
                            "ovr": p["ovr"], "age": p["age"], "dev": p.get("dev"),
                            "starter": pid in ctx["starter_ids"], "cap_hit": ge.cap_hit_now(p, vs),
                            "years_left": ge.years_left(p, vs) if p.get("contract") else None,
                            "tradeable": ok, "why_not": why})
        players.sort(key=lambda p: (-(p["ovr"] or 0), p["name"] or ""))
        picks = [{"draft_season": k[0], "round": k[1], "original_team": ge.abbr(b, k[2]),
                  "label": ge.pick_label(b, k), "booked": k in b["booked_picks"]}
                 for k, owner in sorted(b["picks"].items()) if owner == t]
        out["teams"][ge.abbr(b, t)] = {
            "team_id": t, "abbr": ge.abbr(b, t), "name": ge.team_name(b, t), "gm_name": b["gm"].get(t, {}).get("gm_name"),
            "user_controlled": bool(b["gm"].get(t, {}).get("user_controlled")), "direction": ctx["direction"],
            "needs": [n["group"] for n in ctx["needs"][:4]], "record": b.get("records", {}).get(t),
            "cap_space": (b["cap"].get(t) or {}).get("cap_space"), "players": players, "picks": picks}
    return out
