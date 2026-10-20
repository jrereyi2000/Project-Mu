"""
The Draft Room: GM agents choose picks 1-32 live and JR makes each one in Madden (master D4;
docs/team_management/04_SCOUTING_AND_DRAFT_PLAN.md §2.8; PROJECT_SPEC §2.18, §3.3g, §4.5).

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
"""
import json

from engines import scouting_boards as sb
from engines import scouting_engine as se

ROUND_1 = 32
MAX_DRAFT_DAY_PROPOSALS = 3
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
AFTER_DRAFT = ("After pick 32: Options > User Teams, switch every team back to CPU except the teams you run, then "
               "sim the rest of the draft. Then run Draft results (--mode draft-results) and validate here.")


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
           "trades": draft_day_proposals(cur, _season(cur))}
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
    return [{"id": i, "status": st, "headline": h, "pick": next((int(f[5:]) for f in (fl or []) if f.startswith("pick:")),
                                                                 None)} for i, st, h, fl in cur.fetchall()]


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
    ap.add_argument("action", choices=("state", "lock", "validate"))
    ap.add_argument("--draft-season", type=int)
    ap.add_argument("--dry-run", action="store_true", help="roll back at the end")
    a = ap.parse_args(argv)
    with psycopg.connect(DSN) as conn:
        cur = conn.cursor()
        ds = a.draft_season or _season(cur) + 1
        out = {"state": lambda: state(cur, ds), "lock": lambda: lock_order(cur, ds),
               "validate": lambda: validate(cur, ds)}[a.action]()
        print(json.dumps(out, indent=1, default=str)[:20000])
        conn.rollback() if a.dry_run or a.action == "state" else conn.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
