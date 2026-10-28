"""
The Draft Room: GM agents choose picks 1-32 live and JR makes each one in Madden (master D4;
docs/done/team_management/04_SCOUTING_AND_DRAFT_PLAN.md §2.8; PROJECT_SPEC §2.18, §3.3g, §4.5).

  lock_order    after the Super Bowl load, at offseason stage 4: compute_draft_order mapped through draft_picks
                owners, overall_pick filled for every round of the class; JR can fix a Round 1 owner first; runs the
                draft-day (step 27) board if it isn't stored. Then pick 1's draft-day trade check.
  state         the #debug card: the 32 slots, the pick on the clock (owner, GM, the recommendation = the best
                available on the owner's step-27 board inside the consensus top 80, 3 alternatives, rationale, open
                draft-day proposals), the projected rest of Round 1, the consensus board or any team's board.
  record        JR made pick n in Madden: one scout_draft_picks row (GM = the recommendation, OFF_SCRIPT = someone
                else, JR_TEAM = a team JR controls, whose GM only advises), draft_picks.used, the prospect DRAFTED, in
                the caller's transaction; then pick n+1's draft-day trade check.
  undo_last     the last pick only.
  validate      after the Draft results scan: picks 1-32 vs the recap (MATCH / MISMATCH; nothing else is edited),
                the remaining prospects DRAFTED or UDFA from the recap.
Each pick is decided only when it's on the clock, from the pool still available, so an off-script pick changes every
later recommendation by construction. Recommendations are deterministic (stored boards), so a reload shows the same
player. Draft-day trades: gm_trade_engine.run_draft_day once per pick with board-based premiums, at most
MAX_DRAFT_DAY_PROPOSALS a draft (Q8); JR approves and confirms them through the Front Office.
  start_round   Rounds 2-7 (docs/done/team_management/06_DRAFT_ROUND_TRADES_PLAN.md): before JR sims round n, the round's
                draft-day trades (gm_trade_engine.run_draft_round): teams moving up for a player Mu doesn't expect
                to last to their pick, and teams moving back when theirs will. Who's gone is estimated, never
                captured: Round 1 as recorded, later rounds in Madden's board order (projected_pick), and no
                proposal names a prospect. One row in draft_round_starts per round.
"""
import json

from engines import scouting_boards as sb
from engines import scouting_engine as se

ROUND_1 = 32
LAST_ROUND = 7
MAX_DRAFT_DAY_PROPOSALS = 3
FALL_GAP = 3                    # a team moves up when its target goes 3+ picks before its own pick
DOWN_GAP = 6                    # and moves back when its target lasts 6+ picks past it
SELLER_WINDOW = 4               # a team moving up calls the owners of the 4 slots just ahead of its target's
PREMIUM_PER_GRADE = 0.05
PREMIUM_RANGE = (1.0, 1.6)


class DraftRoomError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


# ------------------------------------------------------------------ pure ----
def next_pick(recorded, n_max=ROUND_1):
    """The pick on the clock: 1 + the picks recorded (they're recorded in order); None after the last."""
    n = len(recorded) + 1
    return n if n <= n_max else None


def recommend(rows, cons, available, n_alt=3):
    """rows: one team's board [(key, team_grade, team_rank)] best first; cons: {key: {rank, grade}}. The best
    available inside the consensus top REACH_LIMIT, then alternatives; when the stored board runs dry, the
    consensus order fills in. Returns (best_key, [alt keys])."""
    keys = [r[0] for r in rows if r[0] in available and cons.get(r[0], {}).get("rank", 9999) <= sb.REACH_LIMIT]
    seen = set(keys)
    keys += [k for k, _ in sorted(cons.items(), key=lambda x: x[1]["rank"]) if k in available and k not in seen]
    return (keys[0], keys[1:1 + n_alt]) if keys else (None, [])


def choice_source(user_controlled, key, recommended):
    if user_controlled:
        return "JR_TEAM"
    return "GM" if key == recommended else "OFF_SCRIPT"


def project(slots, boards, cons, available, start):
    """The rest of Round 1 from pick `start`, each owner taking its recommendation in turn: [(pick, team, key)]."""
    pool = set(available)
    out = []
    for s in slots:
        if s["pick"] < start:
            continue
        best, _ = recommend(boards.get(s["owner_team_id"], []), cons, pool)
        if best is None:
            break
        pool.discard(best)
        out.append((s["pick"], s["owner_team_id"], best))
    return out


def premiums(boards, cons, available, owner):
    """{buyer: multiplier} for the pick on the clock: how much more the buyer's board likes its best available
    player than the consensus's best available (PREMIUM_PER_GRADE a grade point, within PREMIUM_RANGE)."""
    cons_best = max((cons[k]["grade"] for k in available if k in cons), default=None)
    if cons_best is None:
        return {}
    out = {}
    for tid, rows in boards.items():
        if tid == owner:
            continue
        best = next((g for k, g, _ in rows if k in available), None)
        if best is None:
            continue
        lo, hi = PREMIUM_RANGE
        out[tid] = round(min(max(1 + PREMIUM_PER_GRADE * (best - cons_best), lo), hi), 3)
    return out


def estimated_pool(available, projected, round_no):
    """The prospects Mu assumes are left at the start of round_no (2-7), in Madden's board order: the available
    ones (Round 1 as recorded) minus the 32 x (round_no - 2) Madden's CPU is assumed to have taken by its board
    (projected_pick; in 2036 its picks tracked the board at r = 0.93). Unprojected prospects go last."""
    order = sorted(available, key=lambda k: (projected.get(k) is None, projected.get(k) or 0, k))
    return order[ROUND_1 * (round_no - 2):]


def team_target(rows, cons, pool, need_groups, groups):
    """The player a team wants this round: its board's best left in the pool; past its stored board, the
    consensus's best at one of its top two needs; else the consensus's best."""
    k = next((k for k, _, _ in rows if k in pool), None)
    if k is not None:
        return k
    by_cons = sorted((k for k in pool if k in cons), key=lambda k: cons[k]["rank"])
    return next((k for k in by_cons if groups.get(k) in (need_groups or [])[:2]), by_cons[0] if by_cons else None)


def round_candidates(round_slots, pool, targets, fall_gap=FALL_GAP, down_gap=DOWN_GAP, window=SELLER_WINDOW):
    """Trade candidates for a round, best first. round_slots: [{pick, round_pick, key, owner_team_id}] in order;
    pool: the estimated pool in board order (a target's projected slot = its place in it); targets: {team: key}.
    UP: the team's target goes fall_gap+ picks before its first pick in the round, so it calls the owners of the
    `window` slots just ahead of that, nearest first. DOWN: its target lasts down_gap+ picks past its pick, so it
    calls the later teams in the round whose own target won't last to their pick but will to this one, neediest
    first. Each carries gap (how far the move is); a team's calls stay together, and the round makes at most one
    deal per team (gm_trade_engine.run_draft_round)."""
    go = {k: i + 1 for i, k in enumerate(pool)}
    by_slot = {s["round_pick"]: s for s in round_slots}
    first = {}
    for s in round_slots:
        first.setdefault(s["owner_team_id"], s)
    need = {t: s["round_pick"] - go[targets[t]] for t, s in first.items() if go.get(targets.get(t))}
    out = []
    for t, s in first.items():
        g = go.get(targets.get(t))
        if g is None:
            continue
        if s["round_pick"] - g >= fall_gap:
            for j, slot in enumerate(range(max(1, g - 1), max(0, g - 1 - window), -1)):
                seller = by_slot.get(slot)
                if seller and seller["owner_team_id"] != t:
                    out.append({"move": "UP", "buyer": t, "seller": seller["owner_team_id"],
                                "seller_pick": seller["key"], "buyer_pick": s["key"], "overall": seller["pick"],
                                "gap": s["round_pick"] - g, "call": j})
        elif g - s["round_pick"] >= down_gap:
            later = [(need[b["owner_team_id"]], b) for b in first.values()
                     if s["round_pick"] < b["round_pick"] < g and b["owner_team_id"] != t
                     and 0 < need.get(b["owner_team_id"], 0) and go[targets[b["owner_team_id"]]] > s["round_pick"]]
            for j, (_, b) in enumerate(sorted(later, key=lambda x: (-x[0], x[1]["round_pick"]))):
                out.append({"move": "DOWN", "buyer": b["owner_team_id"], "seller": t, "seller_pick": s["key"],
                            "buyer_pick": b["key"], "overall": s["pick"], "gap": g - s["round_pick"], "call": j})
    lead = {}
    for c in out:
        me = c["buyer"] if c["move"] == "UP" else c["seller"]
        lead[me] = max(lead.get(me, 0), c["gap"])
    return sorted(out, key=lambda c: (-lead[c["buyer"] if c["move"] == "UP" else c["seller"]],
                                      c["buyer"] if c["move"] == "UP" else c["seller"], c["call"]))


def round_steps(n):
    """What JR does in Madden for round n's trades (06 plan §1)."""
    end = (f"Sim to the end of Round {n}, then click Start Round {n + 1} here." if n < LAST_ROUND else
           f"Sim to the end of Round {n}: the draft is done. Run Draft results (--mode draft-results) and validate "
           "here.")
    return [f"Approve the Round {n} trades you want in Front Office (unused ones expire when the next round "
            "starts).",
            "For each approved trade: Options > User Teams, put both teams under user control, make the trade in "
            "Madden, then click Made it in Front Office.",
            "Options > User Teams: switch those teams back to CPU, except the teams you run.",
            end]


def validate_rows(recorded, recap):
    """recorded: [{overall_pick, team_id, prospect_id}]; recap: {actual_pick: (prospect_id, drafted_team_id)}.
    Returns [(overall_pick, MATCH | MISMATCH, recap prospect_id, why)]."""
    out = []
    for r in sorted(recorded, key=lambda r: r["overall_pick"]):
        got = recap.get(r["overall_pick"])
        if got is None:
            out.append((r["overall_pick"], "MISMATCH", None, "no recap row for this pick"))
            continue
        pid, team = got
        why = []
        if pid != r["prospect_id"]:
            why.append("different player")
        if team != r["team_id"]:
            why.append("different team")
        out.append((r["overall_pick"], "MISMATCH" if why else "MATCH", pid, ", ".join(why) or None))
    return out


def control_steps(team_name, prospect=None, user_controlled=False):
    """What JR does in Madden for this pick (K4: every Round 1 owner is user-controlled for the draft, clock off)."""
    who = f"{prospect['full_name']} ({prospect['position']}, {prospect['college']})" if prospect else "the player"
    lines = [f"On the clock: the {team_name}. In Madden's draft room, find {who} and draft him."]
    if user_controlled:
        lines.append("This is your team: the GM's pick is advice only. Record whoever you take.")
    return lines


BEFORE_DRAFT = ("Before pick 1: Options > User Teams, turn on user control for every Round 1 owner listed below, and "
                "turn the pick clock off. Leave CPU trades off unless you're making an approved draft-day trade.")
AFTER_DRAFT = ("After pick 32: Options > User Teams, switch every team back to CPU except the teams you run. Then "
               "click Start Round 2 here before you sim: each round's draft-day trades come first, then you sim "
               "that round.")


# -------------------------------------------------------------------- db ----
def installed(cur):
    cur.execute("SELECT to_regclass('scout_draft_picks') IS NOT NULL")
    return bool(cur.fetchone()[0])


def _season(cur):
    return se.current_step(cur)[0]


def r1_slots(cur, draft_season):
    """The 32 Round 1 slots: from draft_picks.overall_pick once locked, else the provisional order."""
    cur.execute("""SELECT overall_pick, original_team_id, owner_team_id, used FROM draft_picks
                   WHERE draft_season = %s AND round = 1 AND overall_pick IS NOT NULL ORDER BY overall_pick""",
                (draft_season,))
    rows = cur.fetchall()
    if len(rows) == ROUND_1:
        return [{"pick": p, "round": 1, "original_team_id": o, "owner_team_id": w, "used": u} for p, o, w, u in rows], True
    from engines.draft_order import draft_order_with_owners
    return [dict(s, used=False) for s in draft_order_with_owners(cur, draft_season)], False


def _teams(cur):
    cur.execute("""SELECT t.id, t.abbreviation, t.city || ' ' || t.name, COALESCE(st.user_controlled, false), g.gm_name,
                          g.voice
                   FROM teams t LEFT JOIN gm_team_settings st ON st.team_id = t.id
                   LEFT JOIN gm_profiles g ON g.team_id = t.id AND g.end_season IS NULL""")
    return {r[0]: {"abbr": r[1], "name": r[2], "user_controlled": r[3], "gm": r[4], "voice": r[5]} for r in cur.fetchall()}


def _board_step(cur, draft_season):
    steps = se.stored_steps(cur, draft_season)
    return steps[-1] if steps else None


def _consensus(cur, draft_season, step):
    cur.execute("""SELECT c.prospect_key, c.rank, c.grade, c.round_label, sp.prospect_id, sp.status, d.full_name,
                          d.position, d.college, d.age
                   FROM scout_consensus c JOIN scout_prospects sp
                     ON sp.draft_season = c.draft_season AND sp.prospect_key = c.prospect_key
                   LEFT JOIN draft_prospects d ON d.id = sp.prospect_id
                   WHERE c.draft_season = %s AND c.step = %s""", (draft_season, step))
    cons, info = {}, {}
    for k, rk, g, lb, pid, st, name, pos, col, age in cur.fetchall():
        cons[k] = {"rank": rk, "grade": float(g)}
        info[k] = {"prospect_key": k, "prospect_id": pid, "status": st, "full_name": name, "position": pos,
                   "college": col, "age": age, "consensus_rank": rk, "grade": float(g), "round_label": lb}
    return cons, info


def _boards(cur, draft_season, step):
    cur.execute("""SELECT team_id, prospect_key, team_grade, team_rank FROM scout_team_boards
                   WHERE draft_season = %s AND step = %s ORDER BY team_id, team_rank""", (draft_season, step))
    out = {}
    for tid, k, g, r in cur.fetchall():
        out.setdefault(tid, []).append((k, float(g), r))
    return out


def _recorded(cur, draft_season):
    cur.execute("""SELECT overall_pick, team_id, prospect_key, prospect_id, choice_source, recommended_key, rationale,
                          rationale_source, madden_match, madden_prospect_id
                   FROM scout_draft_picks WHERE draft_season = %s ORDER BY overall_pick""", (draft_season,))
    cols = ("overall_pick", "team_id", "prospect_key", "prospect_id", "choice_source", "recommended_key", "rationale",
            "rationale_source", "madden_match", "madden_prospect_id")
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def load_room(cur, draft_season):
    step = _board_step(cur, draft_season)
    if step is None:
        raise DraftRoomError(409, f"no scouting board stored for the {draft_season} class")
    cons, info = _consensus(cur, draft_season, step)
    slots, locked = r1_slots(cur, draft_season)
    recorded = _recorded(cur, draft_season)
    taken = {r["prospect_key"] for r in recorded}
    available = {k for k, i in info.items() if i["status"] == "ACTIVE" and k not in taken}
    return {"step": step, "cons": cons, "info": info, "boards": _boards(cur, draft_season, step), "slots": slots,
            "locked": locked, "recorded": recorded, "available": available, "teams": _teams(cur),
            "ctxs": se.team_contexts(cur, _season(cur))}


def _pick_card(room, n, rationale=True):
    slot = next(s for s in room["slots"] if s["pick"] == n)
    owner = slot["owner_team_id"]
    team = room["teams"][owner]
    best, alts = recommend(room["boards"].get(owner, []), room["cons"], room["available"])
    rows = {k: (g, r) for k, g, r in room["boards"].get(owner, [])}
    pub = lambda k: dict(room["info"][k], team_grade=rows.get(k, (None, None))[0], team_rank=rows.get(k, (None, None))[1])
    card = {"pick": n, "owner_team_id": owner, "team": team["abbr"], "team_name": team["name"], "gm": team["gm"],
            "original_team": room["teams"][slot["original_team_id"]]["abbr"],
            "user_controlled": team["user_controlled"],
            "recommendation": pub(best) if best else None, "alternatives": [pub(k) for k in alts],
            "steps": control_steps(team["name"], room["info"].get(best), team["user_controlled"])}
    if rationale and best:
        from engines.scouting_narrative import rationale_template
        card["rationale"] = rationale_template(rationale_sheet(room, owner, best))
    return card


def rationale_sheet(room, owner, key):
    from engines.scouting_value import POSITION_WORDS, school
    i = room["info"][key]
    team = room["teams"][owner]
    sch = school(i["college"])
    ctx = room.get("ctxs", {}).get(owner) or {}
    group = sb.needs_group(i["position"])
    need = group if group in (ctx.get("need_groups") or [])[:3] else None
    return {"gm": team["gm"] or f"The {team['name']}", "voice": team.get("voice"), "team": (team["abbr"], team["name"]),
            "name": (i["full_name"] or "").title(), "position": POSITION_WORDS.get((i["position"] or "").upper(),
                                                                                   "prospect"),
            "school": sch["display"] if sch else (i["college"] or "").title(), "grade_label": i["round_label"],
            "need_group": need}


def state(cur, draft_season=None, team_view=None):
    """Everything the #debug Draft Room card shows (team boards only for the pick on the clock and `team_view`)."""
    draft_season = draft_season or _season(cur) + 1
    if not installed(cur):
        return {"installed": False, "draft_season": draft_season}
    try:
        room = load_room(cur, draft_season)
    except DraftRoomError as e:
        return {"installed": True, "draft_season": draft_season, "error": e.message}
    n = next_pick(room["recorded"])
    by_pick = {r["overall_pick"]: r for r in room["recorded"]}
    slots = []
    for s in room["slots"]:
        r = by_pick.get(s["pick"])
        slots.append({"pick": s["pick"], "team": room["teams"][s["owner_team_id"]]["abbr"],
                      "original_team": room["teams"][s["original_team_id"]]["abbr"],
                      "user_controlled": room["teams"][s["owner_team_id"]]["user_controlled"],
                      "selection": dict(room["info"].get(r["prospect_key"], {}), choice_source=r["choice_source"],
                                        madden_match=r["madden_match"]) if r else None})
    out = {"installed": True, "draft_season": draft_season, "step": room["step"], "locked": room["locked"],
           "before": BEFORE_DRAFT, "after": AFTER_DRAFT, "slots": slots, "on_clock": None, "projected": [],
           "consensus": sorted((room["info"][k] for k in room["available"]), key=lambda i: i["consensus_rank"])[:64],
           "trades": draft_day_proposals(cur, _season(cur)), "rounds_installed": rounds_installed(cur)}
    started = rounds_started(cur, draft_season)
    current = max(started, default=None)
    out["rounds"] = {"started": sorted(started), "current": current,
                     "next": next_round(started) if room["locked"] and n is None and out["rounds_installed"] else None,
                     "steps": round_steps(current) if current else []}
    if n is not None and room["locked"]:
        out["on_clock"] = _pick_card(room, n)
        out["projected"] = [{"pick": p, "team": room["teams"][t]["abbr"], **room["info"][k]}
                            for p, t, k in project(room["slots"], room["boards"], room["cons"], room["available"], n)]
    if team_view:
        tid = next((t for t, v in room["teams"].items() if v["abbr"] == str(team_view).upper()), None)
        if tid is not None:
            out["team_board"] = {"team": room["teams"][tid]["abbr"],
                                 "rows": [dict(room["info"][k], team_grade=g, team_rank=r)
                                          for k, g, r in room["boards"].get(tid, []) if k in room["available"]][:50]}
    return out


def draft_day_proposals(cur, season):
    cur.execute("""SELECT id, status, headline, flags FROM gm_trade_proposals
                   WHERE season = %s AND trade_window = 'DRAFT_DAY' ORDER BY id""", (season,))
    flag = lambda fl, key: next((int(f[len(key) + 1:]) for f in (fl or []) if f.startswith(key + ":")
                                 and f[len(key) + 1:].isdigit()), None)
    return [{"id": i, "status": st, "headline": h, "pick": flag(fl, "pick"), "round": flag(fl, "round") or 1,
             "move": next((f[5:] for f in (fl or []) if f.startswith("move:")), "UP")}
            for i, st, h, fl in cur.fetchall()]


def rounds_installed(cur):
    cur.execute("SELECT to_regclass('draft_round_starts') IS NOT NULL")
    return bool(cur.fetchone()[0])


def rounds_started(cur, draft_season):
    """{round: proposals} for the rounds JR started (2-7)."""
    if not rounds_installed(cur):
        return {}
    cur.execute("SELECT round, proposals FROM draft_round_starts WHERE draft_season = %s", (draft_season,))
    return {r: n for r, n in cur.fetchall()}


def next_round(started):
    """The round Start Round would begin: 2, then each after the last one started; None after Round 7."""
    n = max(started, default=1) + 1
    return n if n <= LAST_ROUND else None


def round_slots(cur, draft_season, n):
    """Round n's slots from the locked order with today's owners: [{pick, round_pick, key, owner_team_id}]."""
    cur.execute("""SELECT overall_pick, original_team_id, owner_team_id FROM draft_picks
                   WHERE draft_season = %s AND round = %s AND overall_pick IS NOT NULL ORDER BY overall_pick""",
                (draft_season, n))
    return [{"pick": p, "round_pick": p - (n - 1) * ROUND_1, "key": (draft_season, n, o), "original_team_id": o,
             "owner_team_id": w} for p, o, w in cur.fetchall()]


def start_round(cur, draft_season, n, llm=None, log=print):
    """JR is about to sim round n (2-7): propose its draft-day trades, once. Returns the new state."""
    from engines import gm_trade_engine as ge
    draft_season = draft_season or _season(cur) + 1
    n = int(n)
    if not rounds_installed(cur):
        raise DraftRoomError(409, "the draft round migration (2027_01_02_draft_round_starts.sql) isn't applied")
    room = load_room(cur, draft_season)
    if not room["locked"]:
        raise DraftRoomError(409, "lock the draft order first")
    if next_pick(room["recorded"]) is not None:
        raise DraftRoomError(409, "record all 32 Round 1 picks first")
    want = next_round(rounds_started(cur, draft_season))
    if want is None or n != want:
        raise DraftRoomError(409, f"Round {want} is next, not {n}" if want else "every round has started")
    slots = round_slots(cur, draft_season, n)
    if len(slots) != ROUND_1:
        raise DraftRoomError(409, f"Round {n} has {len(slots)} locked slots, not {ROUND_1}")
    ids = {i["prospect_id"]: k for k, i in room["info"].items() if i.get("prospect_id")}
    cur.execute("SELECT id, projected_pick FROM draft_prospects WHERE id = ANY(%s)", (list(ids),))
    projected = {ids[i]: p for i, p in cur.fetchall() if p is not None}
    pool = estimated_pool(room["available"], projected, n)
    pool_set = set(pool)
    groups = {k: sb.needs_group(room["info"][k]["position"]) for k in pool}
    targets = {s["owner_team_id"]: team_target(room["boards"].get(s["owner_team_id"], []), room["cons"], pool_set,
                                               (room["ctxs"].get(s["owner_team_id"]) or {}).get("need_groups"), groups)
               for s in slots}
    cands = round_candidates(slots, pool, targets)
    prem = {}
    for c in cands:
        if c["seller"] not in prem:
            prem[c["seller"]] = premiums(room["boards"], room["cons"], pool_set, c["seller"])
        c["premium"] = prem[c["seller"]].get(c["buyer"])
    rep = ge.run_draft_round(cur, _season(cur), draft_season, n, cands,
                             {s["original_team_id"]: s["round_pick"] for s in slots}, llm=llm, log=log,
                             class_names=[i["full_name"] for i in room["info"].values()])
    stored = sum(1 for p in rep["proposals"] if p["id"] is not None)
    cur.execute("""INSERT INTO draft_round_starts (draft_season, round, proposals) VALUES (%s, %s, %s)""",
                (draft_season, n, stored))
    return state(cur, draft_season)


def clock_trades(cur, draft_season, n, room=None, log=print):
    """run_draft_day for pick n, once (skipped when a proposal for the pick exists or the draft's cap is reached),
    with the buyers' premiums from their boards."""
    from engines import gm_trade_engine as ge
    season = _season(cur)
    props = draft_day_proposals(cur, season)
    if any(p["pick"] == n for p in props) or len(props) >= MAX_DRAFT_DAY_PROPOSALS:
        return None
    room = room or load_room(cur, draft_season)
    slot = next((s for s in room["slots"] if s["pick"] == n), None)
    if slot is None:
        return None
    prem = premiums(room["boards"], room["cons"], room["available"], slot["owner_team_id"])
    return ge.run_draft_day(cur, season, {"draft_season": draft_season, "round": 1,
                                          "original_team_id": slot["original_team_id"], "overall_pick": n},
                            log=log, premiums=prem)


def lock_order(cur, draft_season=None, fixes=None, log=print):
    """Fill draft_picks.overall_pick for every round of the class (compute_draft_order through the owners).
    fixes: {pick: team abbreviation} corrections of Round 1 owners JR made against Madden's order screen (K3);
    they're written to draft_picks with source JR. Runs the draft-day board (step 27) if it isn't stored."""
    from engines.draft_order import draft_order_with_owners
    draft_season = draft_season or _season(cur) + 1
    if not installed(cur):
        raise DraftRoomError(409, "the scouting migration (2026_12_12_scouting.sql) isn't applied")
    cur.execute("SELECT count(*) FROM scout_draft_picks WHERE draft_season = %s", (draft_season,))
    if cur.fetchone()[0]:
        raise DraftRoomError(409, "picks are already recorded: the order can't change now")
    teams = {v["abbr"]: t for t, v in _teams(cur).items()}
    slots = draft_order_with_owners(cur, draft_season, rounds=range(1, 8))
    for pick, abbr in (fixes or {}).items():
        tid = teams.get(str(abbr).upper())
        slot = next((s for s in slots if s["pick"] == int(pick) and s["round"] == 1), None)
        if tid is None or slot is None:
            raise DraftRoomError(400, f"can't set pick {pick} to {abbr}")
        cur.execute("""UPDATE draft_picks SET owner_team_id = %s, source = 'JR'
                       WHERE draft_season = %s AND round = 1 AND original_team_id = %s""",
                    (tid, draft_season, slot["original_team_id"]))
        slot["owner_team_id"] = tid
    cur.execute("UPDATE draft_picks SET overall_pick = NULL WHERE draft_season = %s", (draft_season,))
    cur.executemany("""UPDATE draft_picks SET overall_pick = %s
                       WHERE draft_season = %s AND round = %s AND original_team_id = %s""",
                    [(s["pick"], draft_season, s["round"], s["original_team_id"]) for s in slots])
    if se.STEP_DRAFT not in se.stored_steps(cur, draft_season):
        se.catch_up(cur, draft_season, se.STEP_DRAFT, log=log, extras=se.default_extras)
    clock_trades(cur, draft_season, 1, log=log)
    return state(cur, draft_season)


def record(cur, draft_season, n, prospect_key, llm=None, log=print):
    """JR made pick n in Madden. Returns the new state."""
    draft_season = draft_season or _season(cur) + 1
    room = load_room(cur, draft_season)
    if not room["locked"]:
        raise DraftRoomError(409, "lock the draft order first")
    want = next_pick(room["recorded"])
    if want is None or int(n) != want:
        raise DraftRoomError(409, f"pick {want} is on the clock, not {n}")
    if prospect_key not in room["available"]:
        raise DraftRoomError(409, f"{prospect_key} isn't available")
    card = _pick_card(room, want, rationale=False)
    owner = card["owner_team_id"]
    rec = card["recommendation"]["prospect_key"] if card["recommendation"] else None
    source = choice_source(card["user_controlled"], prospect_key, rec)
    from engines.scouting_narrative import draft_rationale
    rat = draft_rationale(rationale_sheet(room, owner, prospect_key), llm)
    slot = next(s for s in room["slots"] if s["pick"] == want)
    cur.execute("""SELECT id FROM gm_trade_proposals WHERE season = %s AND trade_window = 'DRAFT_DAY'
                   AND status = 'EXECUTED' AND %s = ANY(flags) ORDER BY id DESC LIMIT 1""",
                (_season(cur), f"pick:{want}"))
    tp = cur.fetchone()
    cur.execute("""INSERT INTO scout_draft_picks (draft_season, overall_pick, round, team_id, original_team_id,
                       prospect_key, prospect_id, recommended_key, choice_source, alternatives, rationale,
                       rationale_source, trade_proposal_id)
                   VALUES (%s, %s, 1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (draft_season, want, owner, slot["original_team_id"], prospect_key,
                 room["info"][prospect_key]["prospect_id"], rec, source,
                 json.dumps([a["prospect_key"] for a in card["alternatives"]]), rat["text"], rat["source"],
                 tp[0] if tp else None))
    cur.execute("""UPDATE draft_picks SET used = true WHERE draft_season = %s AND round = 1 AND original_team_id = %s""",
                (draft_season, slot["original_team_id"]))
    cur.execute("""UPDATE scout_prospects SET status = 'DRAFTED' WHERE draft_season = %s AND prospect_key = %s""",
                (draft_season, prospect_key))
    if want < ROUND_1:
        clock_trades(cur, draft_season, want + 1, log=log)
    return state(cur, draft_season)


def undo_last(cur, draft_season=None, n=None):
    """Undo the last recorded pick; with n, only if n is that pick."""
    draft_season = draft_season or _season(cur) + 1
    rec = _recorded(cur, draft_season)
    if not rec:
        raise DraftRoomError(409, "no pick to undo")
    last = rec[-1]
    if n is not None and int(n) != last["overall_pick"]:
        raise DraftRoomError(409, f"only the last recorded pick ({last['overall_pick']}) can be undone")
    cur.execute("DELETE FROM scout_draft_picks WHERE draft_season = %s AND overall_pick = %s",
                (draft_season, last["overall_pick"]))
    cur.execute("""UPDATE draft_picks SET used = false WHERE draft_season = %s AND round = 1 AND overall_pick = %s""",
                (draft_season, last["overall_pick"]))
    cur.execute("""UPDATE scout_prospects SET status = 'ACTIVE' WHERE draft_season = %s AND prospect_key = %s""",
                (draft_season, last["prospect_key"]))
    return state(cur, draft_season)


def validate(cur, draft_season):
    """After resolve_and_load_picks: picks 1-32 vs the recap (madden_match, madden_prospect_id), the rest of the
    class DRAFTED or UDFA from the recap. Never edits actual_* (the recap is Madden's truth). Returns a report."""
    if not installed(cur):
        return None
    rec = _recorded(cur, draft_season)
    cur.execute("""SELECT actual_pick, id, drafted_team_id FROM draft_prospects
                   WHERE draft_season = %s AND actual_pick IS NOT NULL""", (draft_season,))
    recap = {p: (pid, t) for p, pid, t in cur.fetchall()}
    if not recap:
        return {"draft_season": draft_season, "recap": 0}
    rows = validate_rows(rec, recap)
    cur.executemany("""UPDATE scout_draft_picks SET madden_match = %s, madden_prospect_id = %s
                       WHERE draft_season = %s AND overall_pick = %s""",
                    [(m, pid, draft_season, n) for n, m, pid, _ in rows])
    cur.execute("""UPDATE scout_prospects sp SET status = CASE WHEN d.actual_pick IS NOT NULL THEN 'DRAFTED' ELSE 'UDFA' END
                   FROM draft_prospects d WHERE d.id = sp.prospect_id AND sp.draft_season = %s
                     AND sp.status IN ('ACTIVE', 'DRAFTED')""", (draft_season,))
    mism = [{"pick": n, "why": why} for n, m, _, why in rows if m == "MISMATCH"]
    return {"draft_season": draft_season, "recap": len(recap), "checked": len(rows),
            "match": sum(1 for r in rows if r[1] == "MATCH"), "mismatches": mism}


def main(argv=None):
    import argparse
    import psycopg
    from pipeline.load_capture import DSN
    ap = argparse.ArgumentParser(description="The Draft Room (team management step 4)")
    ap.add_argument("action", choices=("state", "lock", "validate", "start-round"))
    ap.add_argument("--draft-season", type=int)
    ap.add_argument("--round", type=int, help="start-round: the round JR is about to sim (2-7)")
    ap.add_argument("--dry-run", action="store_true", help="roll back at the end")
    a = ap.parse_args(argv)
    with psycopg.connect(DSN) as conn:
        cur = conn.cursor()
        ds = a.draft_season or _season(cur) + 1
        out = {"state": lambda: state(cur, ds), "lock": lambda: lock_order(cur, ds),
               "validate": lambda: validate(cur, ds),
               "start-round": lambda: start_round(cur, ds, a.round)}[a.action]()
        print(json.dumps(out, indent=1, default=str)[:20000])
        conn.rollback() if a.dry_run or a.action == "state" else conn.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
