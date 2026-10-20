"""
The moves a trade makes (docs/team_management/03_GM_AGENTS_PLAN.md §2.7, Phase 5; PROJECT_SPEC §3.3f).

The League Transactions scan skips trade rows (JR, 2026-10-19), so every trade's moves are made here when JR
confirms it on #debug: an approved proposal ("Made it"), a different trade on a proposal's card ("Made a
different trade"), a trade he records himself ("Record a trade") or an unmatched trade row the scan saw
("Apply this trade"). Every trade moves its players (JR, 2026-10-20).

plan_trade_ops(proposal, state) is pure (state in, operations out); execute_trade(cur, ops) runs them inside the
caller's DB transaction (nothing here commits):
  - player_seasons.team_id -> the receiver (released_week cleared);
  - the ACTIVE contract -> TRADED, and a row-for-row copy on the new team (source TRANSACTION, every
    player_contract_years row with its projected flag, the extension terms) through contracts.insert_contract;
  - both teams' team_cap_seasons (the new season's in the offseason, when the Season scan has made it): salaries
    -/+ that season's hit, the sender's cap_penalty + dead money
    (TRADE_DEAD_MONEY: the contract's cut_penalty, C4), total_spend and cap_space to match, derived_at stamped;
  - draft_picks.owner_team_id -> the receiver, source TRADE, acquired_proposal_id;
  - one transactions row per leg (type TRADED, trade_group gm:<id>, row_key gm:<id>:<n>, capture_id NULL);
  - the proposal -> EXECUTED (executed_as PROPOSED | JR_EDITED | SIGHTING).
"""
TRADE_DEAD_MONEY = "cut_penalty"         # C4 (sub-plan §1.1): the sending team takes a cap penalty
ROUND_WORDS = {1: "1ST", 2: "2ND", 3: "3RD", 4: "4TH", 5: "5TH", 6: "6TH", 7: "7TH"}


class TradeError(Exception):
    """The trade can't be planned (nothing is written)."""


def heading(season, week):
    """txn_date for the legs: '<season> SEASON - WEEK <n>' in the regular season, else the offseason."""
    if week and 1 <= week <= 18:
        return f"{season} SEASON - WEEK {week}"
    return f"{season} OFFSEASON"


def leg_text(asset, state):
    """Synthesized raw_text for one leg, in the list's own grammar ('PANTHERS TRADE (WR) NAME TO SAINTS')."""
    nick = lambda t: (state["teams"].get(t, {}).get("name") or str(t)).upper()
    if asset["type"] == "PLAYER":
        p = state["players"].get(asset["player_id"], {})
        name = (p.get("name") or str(asset["player_id"])).upper()
        return f"{nick(asset['from'])} TRADE ({p.get('pos') or '?'}) {name} TO {nick(asset['to'])}"
    ds, rd, orig = asset["pick"]
    own = "" if orig == asset["from"] else f" ({nick(orig)})"
    return f"{nick(asset['from'])} TRADE {ds} {ROUND_WORDS.get(rd, rd)} ROUND PICK{own} TO {nick(asset['to'])}"


def plan_trade_ops(proposal, state, executed_as="PROPOSED"):
    """proposal: {id, season, week, assets: [{type, player_id | pick, from, to}]}. state: {teams {id: {name}},
    players {id: {team_id, pos, name, contract | None}}, cap_rows {team_id}, picks {(ds, rd, orig): owner}}.
    Returns (ops, warnings). Warnings never stop a confirmed trade (Madden is the truth once JR made it)."""
    pid, season, week = proposal["id"], proposal["season"], proposal["week"]
    cap_season = state.get("cap_season", season)            # the offseason trades the new year's cap
    ops, warnings = [], []
    caps = {}
    if not proposal.get("assets"):
        raise TradeError("a trade needs at least one asset")
    for n, x in enumerate(proposal["assets"], 1):
        frm, to = x["from"], x["to"]
        if frm == to:
            raise TradeError(f"leg {n} goes from a team to itself")
        if x["type"] == "PLAYER":
            p = state["players"].get(x["player_id"])
            if p is None:
                raise TradeError(f"player {x['player_id']} has no row this season")
            if p.get("team_id") != frm:
                warnings.append(f"{p.get('name')}: on team {p.get('team_id')} in Mu, not {frm} (moved anyway)")
            ops.append({"op": "move", "player": x["player_id"], "team_id": to})
            c = p.get("contract")
            if c:
                hit = (c.get("years") or {}).get(cap_season, (0, False))[0] or 0
                dead = (c.get("cut_penalty") or 0) if TRADE_DEAD_MONEY == "cut_penalty" else 0
                ops.append({"op": "contract_trade", "player": x["player_id"], "old_contract_id": c["contract_id"],
                            "team_id": to, "copy": {k: c.get(k) for k in (
                                "season", "length", "years_remaining", "total_value", "bonus", "cut_savings",
                                "cut_penalty", "no_trade_clause", "incentive", "extension_years", "extension_value",
                                "extended_transaction_id")}, "years": dict(c.get("years") or {})})
                caps.setdefault(frm, {"salaries": 0, "cap_penalty": 0})
                caps.setdefault(to, {"salaries": 0, "cap_penalty": 0})
                caps[frm]["salaries"] -= hit
                caps[frm]["cap_penalty"] += dead
                caps[to]["salaries"] += hit
            else:
                warnings.append(f"{p.get('name')}: no ACTIVE contract (no contract or cap moves)")
        else:
            key = tuple(x["pick"])
            owner = state["picks"].get(key)
            if owner is None:
                warnings.append(f"pick {key} isn't in draft_picks (inserted)")
            elif owner != frm:
                warnings.append(f"pick {key} belongs to team {owner} in Mu, not {frm} (moved anyway)")
            ops.append({"op": "pick_owner", "pick": key, "owner": to, "known": owner is not None})
        ops.append({"op": "txn", "leg": n, "season": season, "week": week if week and week <= 18 else None,
                    "txn_date": heading(season, week), "row_key": f"gm:{pid}:{n}", "trade_group": f"gm:{pid}",
                    "player": x.get("player_id"), "player_name": (state["players"].get(x.get("player_id")) or {}).get("name"),
                    "position": (state["players"].get(x.get("player_id")) or {}).get("pos"),
                    "from_team_id": frm, "to_team_id": to, "raw_text": leg_text(x, state)})
    for t, d in sorted(caps.items()):
        if t not in state.get("cap_rows", set()):
            warnings.append(f"team {t} has no {cap_season} cap row (no cap delta; the Season scan sets it)")
            continue
        ops.append({"op": "cap_delta", "team_id": t, "season": cap_season, "salaries": d["salaries"],
                    "cap_penalty": d["cap_penalty"]})
    ops.append({"op": "proposal_status", "id": pid, "executed_as": executed_as})
    return ops, warnings


def read_state(cur, season, proposal, cap_season=None):
    """plan_trade_ops' state for the proposal's teams, players and picks (read-only). cap_season: the season whose
    cap rows the trade moves (the next one in the offseason; default `season`)."""
    cap_season = cap_season or season
    teams_ids = {x["from"] for x in proposal["assets"]} | {x["to"] for x in proposal["assets"]}
    cur.execute("SELECT id, abbreviation, name FROM teams WHERE id = ANY(%s)", (list(teams_ids),))
    teams = {r[0]: {"abbr": r[1], "name": r[2]} for r in cur.fetchall()}
    pids = [x["player_id"] for x in proposal["assets"] if x["type"] == "PLAYER"]
    players = {}
    if pids:
        cur.execute("""SELECT ps.player_id, ps.team_id, ps.position, COALESCE(p.full_name, p.player_name)
                       FROM player_seasons ps JOIN players p ON p.id = ps.player_id
                       WHERE ps.season = %s AND ps.player_id = ANY(%s)""", (season, pids))
        for pid, tid, pos, name in cur.fetchall():
            players[pid] = {"team_id": tid, "pos": pos, "name": name, "contract": None}
        cur.execute("""SELECT id, player_id, season, length, years_remaining, total_value, bonus, cut_savings,
                              cut_penalty, no_trade_clause, incentive, extension_years, extension_value,
                              extended_transaction_id
                       FROM player_contracts WHERE season = %s AND status = 'ACTIVE' AND player_id = ANY(%s)""",
                    (season, pids))
        cols = ("contract_id", "player_id", "season", "length", "years_remaining", "total_value", "bonus",
                "cut_savings", "cut_penalty", "no_trade_clause", "incentive", "extension_years", "extension_value",
                "extended_transaction_id")
        for r in cur.fetchall():
            c = dict(zip(cols, r), years={})
            if c["player_id"] in players:
                players[c["player_id"]]["contract"] = c
                cur.execute("SELECT year, cap_hit, projected FROM player_contract_years WHERE contract_id = %s",
                            (c["contract_id"],))
                c["years"] = {y: (h, bool(pr)) for y, h, pr in cur.fetchall()}
    cur.execute("SELECT team_id FROM team_cap_seasons WHERE season = %s AND team_id = ANY(%s)",
                (cap_season, list(teams_ids)))
    cap_rows = {r[0] for r in cur.fetchall()}
    picks = {}
    keys = [tuple(x["pick"]) for x in proposal["assets"] if x["type"] == "PICK"]
    for ds, rd, orig in keys:
        cur.execute("SELECT owner_team_id FROM draft_picks WHERE draft_season = %s AND round = %s AND original_team_id = %s",
                    (ds, rd, orig))
        row = cur.fetchone()
        if row:
            picks[(ds, rd, orig)] = row[0]
    return {"teams": teams, "players": players, "cap_rows": cap_rows, "picks": picks, "cap_season": cap_season}


def execute_trade(cur, ops, season):
    """Run plan_trade_ops' ops inside the caller's transaction. Returns {transactions: [ids], contracts: [ids]}."""
    from pipeline.contracts import insert_contract
    out = {"transactions": [], "contracts": []}
    first_txn = None
    pid = next(op["id"] for op in ops if op["op"] == "proposal_status")
    for op in ops:                                       # transactions rows first: the cap stamps point at them
        if op["op"] == "txn":
            cur.execute("""INSERT INTO transactions (season, week, txn_date, type, player_id, player_name, position,
                               from_team_id, to_team_id, raw_text, row_key, seq, parse_ok, flags, applied, trade_group,
                               gm_proposal_id, capture_id)
                           VALUES (%s, %s, %s, 'TRADED', %s, %s, %s, %s, %s, %s, %s, 1, true, NULL, true, %s, %s, NULL)
                           ON CONFLICT (season, txn_date, row_key, seq) DO UPDATE SET gm_proposal_id = EXCLUDED.gm_proposal_id
                           RETURNING id""",
                        (op["season"], op["week"], op["txn_date"], op["player"], op["player_name"], op["position"],
                         op["from_team_id"], op["to_team_id"], op["raw_text"], op["row_key"], op["trade_group"], pid))
            tid = cur.fetchone()[0]
            out["transactions"].append(tid)
            first_txn = first_txn or tid
    for op in ops:
        kind = op["op"]
        if kind == "move":
            cur.execute("""UPDATE player_seasons SET team_id = %s, released_week = NULL
                           WHERE player_id = %s AND season = %s""", (op["team_id"], op["player"], season))
        elif kind == "contract_trade":
            cur.execute("UPDATE player_contracts SET status = 'TRADED' WHERE id = %s AND status = 'ACTIVE'",
                        (op["old_contract_id"],))
            c = op["copy"]
            years = {y: h for y, (h, _) in op["years"].items()}
            cid = insert_contract(cur, player_id=op["player"], team_id=op["team_id"], season=c["season"],
                                  length=c["length"], years_remaining=c["years_remaining"],
                                  total_value=c["total_value"], bonus=c["bonus"], cut_savings=c["cut_savings"],
                                  cut_penalty=c["cut_penalty"], no_trade_clause=c["no_trade_clause"],
                                  incentive=c["incentive"], years=years, source="TRANSACTION")
            cur.execute("""UPDATE player_contracts SET extension_years = %s, extension_value = %s,
                                  extended_transaction_id = %s WHERE id = %s""",
                        (c.get("extension_years"), c.get("extension_value"), c.get("extended_transaction_id"), cid))
            projected = [y for y, (_, pr) in op["years"].items() if pr]
            if projected:
                cur.execute("UPDATE player_contract_years SET projected = true WHERE contract_id = %s AND year = ANY(%s)",
                            (cid, projected))
            out["contracts"].append(cid)
        elif kind == "cap_delta":
            d = op["salaries"] + op["cap_penalty"]
            cur.execute("""UPDATE team_cap_seasons SET salaries = salaries + %s, cap_penalty = cap_penalty + %s,
                                  total_spend = total_spend + %s, cap_space = cap_space - %s,
                                  derived_at = now(), derived_transaction_id = %s
                           WHERE team_id = %s AND season = %s""",
                        (op["salaries"], op["cap_penalty"], d, d, first_txn, op["team_id"], op.get("season", season)))
        elif kind == "pick_owner":
            ds, rd, orig = op["pick"]
            cur.execute("""INSERT INTO draft_picks (draft_season, round, original_team_id, owner_team_id, source,
                                                    acquired_proposal_id)
                           VALUES (%s, %s, %s, %s, 'TRADE', %s)
                           ON CONFLICT (draft_season, round, original_team_id) DO UPDATE SET
                               owner_team_id = EXCLUDED.owner_team_id, source = 'TRADE',
                               acquired_proposal_id = EXCLUDED.acquired_proposal_id""",
                        (ds, rd, orig, op["owner"], pid))
        elif kind == "proposal_status":
            cur.execute("""UPDATE gm_trade_proposals SET status = 'EXECUTED', executed_at = now(), executed_as = %s
                           WHERE id = %s""", (op["executed_as"], op["id"]))
    return out
