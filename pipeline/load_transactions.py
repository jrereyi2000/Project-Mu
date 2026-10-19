"""
Load transactions.csv (docs/team_management/02_LEAGUE_TRANSACTIONS_PLAN.md, phase 4; PROJECT_SPEC §3.3e).

Called by pipeline/load_capture.py inside its one DB transaction: from load_weekly before Season Stats (so a
moved player's week lands on his new team), or alone for a transactions-only folder. plan_effects() is pure
(in-memory state in, operations out) and holds every rule; load_transactions() reads the state, runs the plan
and executes it.

Rows are keyed (season, txn_date, row_key, seq) and inserted ON CONFLICT DO NOTHING; effects (team moves,
contracts, cap deltas) run only for rows inserted now, oldest first, so loading the same rows again changes
nothing. There are no automatic Team Salary re-scans (JR, 2026-10-19): contracts and team_cap_seasons are kept
current from the transactions themselves, and the next Season scan replaces all of it. Rows before the effects cutoff (first load of a season: '<season> SEASON - WEEK 1') and rows
from earlier seasons are stored with applied = false, as history only.
"""
import csv, json, os

from pipeline.capture_matching import db_player_name, full_key, name_case, name_key
from pipeline.transactions_parse import (assign_seq, clean_heading, heading_ordinal, heading_week, heading_year, is_move,
                                         legs, parse_row, row_key)

OFFSEASON_WEEK = 24          # docs/EDITORIAL_MATRIX.md: week 24 is the offseason (SB = 23)


class TransactionsError(Exception):
    """A check failed: nothing is written (load_capture turns it into a LoadError)."""


def effective_week(heading):
    """released_week for a heading: games.week, 0 in the preseason, 24 in the offseason."""
    w = heading_week(heading)
    if w is not None:
        return w
    o = heading_ordinal(heading)
    return 0 if o and o[1] == 0 else OFFSEASON_WEEK


# ------------------------------------------------------------------ rows ----
def read_rows(folder):
    """transactions.csv -> list rows newest first: [{heading, text, flags}]."""
    with open(os.path.join(folder, "transactions.csv"), newline="") as fh:
        rows = list(csv.DictReader(fh))
    rows.sort(key=lambda r: int(r.get("ORDER") or 0))
    return [{"heading": clean_heading(r["HEADING"]), "text": r["TEXT"], "flags": [f for f in (r.get("FLAGS") or "").split(",") if f]}
            for r in rows]


def read_meta(folder):
    try:
        with open(os.path.join(folder, "transactions", "meta.json")) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def expand(rows, meta=None, skipped=None):
    """List rows -> transaction legs (newest first) with their key fields. A scan that stopped at --since (didn't
    reach the end of the list) read its oldest heading group only partly, which would shift that group's seq, so
    every row older than `since` is left out (it was loaded by an earlier scan). Rows that aren't roster moves
    (transactions_parse.is_move: the draft-class notice and the like) are skipped, not stored (JR, 2026-10-19);
    their texts go into `skipped` when a list is passed."""
    meta = meta or {}
    cut = heading_ordinal(meta["since"]) if meta.get("since") and not meta.get("reached_end") else None
    out = []
    for r in rows:
        if cut is not None and (heading_ordinal(r["heading"]) or cut) < cut:
            continue
        if not is_move(r["text"]):
            if skipped is not None:
                skipped.append(r["text"])
            continue
        p = parse_row(r["text"])
        base = row_key(p, r["text"])
        for lg in legs(p):
            kind = lg["type"] if (p["ok"] or p["type"] == "TRADED") else "OTHER"
            out.append({"heading": r["heading"], "text": p["text"], "row_key": base + lg["suffix"], "parsed": p,
                        "leg": dict(lg, type=kind), "parse_ok": p["ok"],
                        "flags": list(dict.fromkeys(r.get("flags", []) + p["flags"]))})
    for e, s in zip(out, assign_seq(out)):
        e["seq"] = s
    _trade_groups(out)
    return out


def _trade_groups(entries):
    """Consecutive TRADED legs between the same two teams in one heading group share a trade_group
    '{season}:{txn_date}:{A}-{B}:{n}' (teams sorted, n counts the heading's trades from 1)."""
    n_by_heading, prev = {}, None
    for e in entries:
        lg = e["leg"]
        if lg["type"] != "TRADED" or not (lg["from"] and lg["to"]):
            prev = None
            continue
        pair = tuple(sorted((lg["from"], lg["to"])))
        if prev and prev[0] == e["heading"] and prev[1] == pair:
            e["trade_group"] = prev[2]
            continue
        n = n_by_heading[e["heading"]] = n_by_heading.get(e["heading"], 0) + 1
        g = f"{heading_year(e['heading'])}:{e['heading']}:{pair[0]}-{pair[1]}:{n}"
        e["trade_group"], prev = g, (e["heading"], pair, g)


# ---------------------------------------------------------------- shares ----
MIN_SHARE_SAMPLES = 10       # fewer scanned contracts of a length than this: split a new deal evenly


def average_shares(contracts):
    """{length: [average share of the total in year 1, 2, ...]} from scanned contracts whose every year is on
    screen (years_remaining == length, at most 5 years), with the sample count: {length: (shares, n)}.
    contracts: [(length, [cap hit year 1, year 2, ...])]."""
    sums, counts = {}, {}
    for length, hits in contracts:
        if not length or length > 5 or len(hits) < length:
            continue
        hits = [h or 0 for h in hits[:length]]
        total = sum(hits)
        if total <= 0:
            continue
        acc = sums.setdefault(length, [0.0] * length)
        for i, h in enumerate(hits):
            acc[i] += h / total
        counts[length] = counts.get(length, 0) + 1
    return {n: ([v / counts[n] for v in acc], counts[n]) for n, acc in sums.items()}


def year_shares(n, shares, min_samples=MIN_SHARE_SAMPLES):
    """Each year's share of an n-year deal: the scanned average for that length when at least min_samples
    contracts back it, else even. Always sums to 1."""
    got = shares.get(n) if shares else None
    if not got or got[1] < min_samples or len(got[0]) != n or sum(got[0]) <= 0:
        return [1.0 / n] * n
    total = sum(got[0])
    return [v / total for v in got[0]]


def projected_hits(value, n, first_year, shares):
    """{year: projected cap hit} for an n-year deal worth `value` starting in first_year (JR, 2026-10-19:
    amount split by the scanned per-length shares, else evenly). Rounded to whole dollars, summing to value."""
    sh = year_shares(n, shares)
    hits = [int(round(value * x)) for x in sh]
    hits[-1] += value - sum(hits)
    return {first_year + i: h for i, h in enumerate(hits)}


# ----------------------------------------------------------------- state ----
def _new_state(season, team_ids, players, existing=(), group_counts=None, contracts=None, apply_from=None,
               shares=None):
    """In-memory state for plan_effects.
    players: [{id, player_name, full_name, team_id, position, released_week, this_season(bool)}]
    existing: {(season, txn_date, row_key, seq)}; group_counts: {(season, txn_date): rows stored}
    contracts: None when player_contracts doesn't exist (step 1 not loaded), else {player_id: contract dict with
    id, team_id, length, years_remaining, total_value, bonus, cut_savings, cut_penalty, no_trade_clause,
    incentive, years {year: cap_hit}, projected [years]}.
    shares: average_shares() of the scanned contracts, for projecting re-signings."""
    ps = {}
    for p in players:
        p = dict(p, full_key=full_key(p.get("full_name")), name_key=name_key(p.get("player_name")))
        ps[p["id"]] = p
    return {"season": season, "teams": dict(team_ids), "players": ps, "existing": set(existing),
            "group_counts": dict(group_counts or {}), "contracts": contracts, "apply_from": apply_from, "next_tmp": -1,
            "shares": shares or {}}


def _narrow(hits, pos):
    if len(hits) > 1 and pos:
        same = [h for h in hits if h["position"] == pos]
        if same:
            return same
    return hits


def _resolve_sign(state, name, pos):
    fk = full_key(name)
    tiers = [lambda p: p["this_season"] and p["released_week"] is not None,
             lambda p: p["this_season"],
             lambda p: not p["this_season"]]
    for i, tier in enumerate(tiers):
        hits = [p for p in state["players"].values() if tier(p) and p["full_key"] == fk]
        if i:                                       # tiers 2-3 need the position too (plan §2.3)
            hits = [p for p in hits if not pos or p["position"] == pos]
        hits = _narrow(hits, pos)
        if len(hits) == 1:
            return hits[0], None
        if len(hits) > 1:
            return None, f"{len(hits)} players named {name}"
    return None, None


def _resolve_on_team(state, name, pos, team_id):
    """full name, then list name (initial + last), narrowed by position: on the team this season first, then
    league-wide this season."""
    fk, nk = full_key(name), name_key(name)
    for scope in (lambda p: p["this_season"] and p["team_id"] == team_id, lambda p: p["this_season"]):
        for key, val in (("full_key", fk), ("name_key", nk)):
            hits = _narrow([p for p in state["players"].values() if scope(p) and p[key] == val], pos)
            if len(hits) == 1:
                return hits[0], None
            if len(hits) > 1:
                return None, f"{len(hits)} players named {name}"
    return None, None


def _split_name(name):
    words = name_case((name or "").title()).split()
    return (words[0], " ".join(words[1:])) if len(words) > 1 else ("", " ".join(words))


# ------------------------------------------------------------------ plan ----
def plan_effects(entries, state, money=None):
    """entries (expand(), newest first) + state -> (plan, report). plan is a list, oldest first, of
    {"txn": {...transactions row...}, "ops": [...]} for rows not stored yet; ops are
      create_player {tmp, player_name, full_name, team_id, position, released_week}
      move {player, team_id}                     (player_seasons.team_id, released_week NULL)
      release {player, week}                     (player_seasons.released_week; team_id kept)
      contract_status {player, status}           (his ACTIVE contract -> SUPERSEDED / RELEASED / TRADED)
      contract_insert {player, team_id, length, years_remaining, total_value, bonus, cut_savings, cut_penalty,
                       no_trade_clause, incentive, years, projected}   (source TRANSACTION; projected = years)
      contract_extend {player, years, value, hits}  (re-sign / extension: terms on the ACTIVE contract, projected
                                                 cap hits for the new deal's years)
      cap_delta {team_id, salaries, cap_penalty} (team_cap_seasons for the season, marked derived)
    `player` is a players id, or a negative placeholder for a player created earlier in the plan.
    money: parse_money (pipeline/money.py); None skips contract inserts that need it.
    Raises TransactionsError on a failed check (nothing may be written)."""
    season = state["season"]
    report = {"transactions_seen": len(entries), "transactions_new": 0, "unparsed": [], "ambiguous": [],
              "created_players": [], "not_applied": 0, "older_seasons": 0, "contract_ops": 0, "cap_deltas": 0}
    problems = []

    # checks
    counts = {}
    for e in entries:
        y = heading_year(e["heading"])
        if y is None:
            problems.append(f"unreadable heading {e['heading']!r} ({e['text'][:60]})")
        elif y > season:
            problems.append(f"heading {e['heading']!r} is after season {season}")
        counts[(y, e["heading"])] = counts.get((y, e["heading"]), 0) + 1
        lg = e["leg"]
        for t in (lg["from"], lg["to"]):
            if e["parse_ok"] and t and t not in state["teams"]:
                problems.append(f"team {t} isn't in the teams table ({e['text'][:60]})")
    for k, n in counts.items():
        stored = state["group_counts"].get(k, 0)
        if n < stored:
            problems.append(f"{k[1]}: {n} rows in this scan, {stored} already stored (the scan missed rows: re-capture)")
    if problems:
        raise TransactionsError("transactions checks failed:\n  " + "\n  ".join(problems))

    cut = heading_ordinal(state["apply_from"]) if state["apply_from"] else None
    teams = state["teams"]
    plan = []
    for e in reversed(entries):                      # oldest first
        y = heading_year(e["heading"])
        key = (y, e["heading"], e["row_key"], e["seq"])
        if key in state["existing"]:
            continue
        state["existing"].add(key)
        lg, kind = e["leg"], e["leg"]["type"]
        applied = y == season and (cut is None or heading_ordinal(e["heading"]) >= cut)
        if y != season:
            report["older_seasons"] += 1
        if not applied:
            report["not_applied"] += 1
        if not e["parse_ok"]:
            report["unparsed"].append(e["text"])
        frm, to = teams.get(lg["from"]), teams.get(lg["to"])
        if kind in ("RE_SIGNED", "EXTENDED"):
            frm = None
        value = None
        if lg["amount_text"] and money:
            try:
                value = money("$" + lg["amount_text"])
            except ValueError:
                e["flags"].append("bad_amount")
        txn = {"season": y, "week": heading_week(e["heading"]), "txn_date": e["heading"], "type": kind,
               "player": None, "player_name": lg["name"] if kind != "TRADED" or lg["name"] else lg["asset"],
               "position": lg["pos"], "from_team_id": frm, "to_team_id": to, "contract_years": lg["years"],
               "contract_value": value, "raw_text": e["text"], "row_key": e["row_key"], "seq": e["seq"],
               "parse_ok": e["parse_ok"], "flags": e["flags"], "applied": applied,
               "trade_group": e.get("trade_group")}
        entry = {"txn": txn, "ops": []}
        plan.append(entry)
        report["transactions_new"] += 1
        if not applied or kind == "OTHER" or not lg["name"] or (kind == "TRADED" and not e["parse_ok"]):
            continue
        _plan_one(state, entry, kind, lg, frm, to, e, report, value)
    report["contract_ops"] = sum(1 for en in plan for op in en["ops"] if op["op"].startswith("contract"))
    report["cap_deltas"] = sum(1 for en in plan for op in en["ops"] if op["op"] == "cap_delta")
    return plan, report


def _hit(c, season):
    return (c.get("years") or {}).get(season) or 0 if c else 0


def _plan_one(state, entry, kind, lg, frm, to, e, report, value):
    season, ops, txn = state["season"], entry["ops"], entry["txn"]
    week = effective_week(e["heading"])
    contracts = state["contracts"]
    if kind == "SIGNED":
        p, why = _resolve_sign(state, lg["name"], lg["pos"])
    else:
        p, why = _resolve_on_team(state, lg["name"], lg["pos"], frm if kind in ("RELEASED", "TRADED") else to)
    if why:
        report["ambiguous"].append(f"{e['heading']}: {e['text']} ({why})")
        txn["flags"] = txn["flags"] + ["ambiguous"]
        return
    if p is None and kind in ("RE_SIGNED", "EXTENDED"):
        report["ambiguous"].append(f"{e['heading']}: {e['text']} (no such player)")
        return
    if p is None:                                    # a player the roster scan never saw
        first, last = _split_name(lg["name"])
        tmp = state["next_tmp"]
        state["next_tmp"] -= 1
        home = to if kind == "SIGNED" else frm
        p = {"id": tmp, "player_name": db_player_name(first, last) if first else last,
             "full_name": f"{first} {last}".strip(), "team_id": home, "position": lg["pos"], "released_week": None,
             "this_season": True}
        p["full_key"], p["name_key"] = full_key(p["full_name"]), name_key(p["player_name"])
        state["players"][tmp] = p
        ops.append({"op": "create_player", "tmp": tmp, "player_name": p["player_name"], "full_name": p["full_name"],
                    "team_id": home, "position": lg["pos"]})
        report["created_players"].append(f"{p['full_name']} ({lg['pos']}, {lg['to'] or lg['from']})")
    pid = p["id"]
    txn["player"] = pid
    active = contracts.get(pid) if contracts is not None else None

    if kind == "SIGNED":
        ops.append({"op": "move", "player": pid, "team_id": to, "position": lg["pos"]})
        p.update(team_id=to, released_week=None, this_season=True)
        if contracts is not None:
            if active:                               # an ACTIVE deal elsewhere (not released first): it ends
                ops.append({"op": "contract_status", "player": pid, "status": "SUPERSEDED"})
                if _hit(active, season):
                    ops.append({"op": "cap_delta", "team_id": active["team_id"], "salaries": -_hit(active, season),
                                "cap_penalty": 0})
                contracts.pop(pid, None)
            n, total = lg["years"], value
            if n and total is not None:              # CPU free-agent deals are 1 year (master §3.3)
                years = {season + i: (round(total / n) if i < n else 0) for i in range(max(5, n))}
                c = {"team_id": to, "length": n, "years_remaining": n, "total_value": total, "bonus": None,
                     "cut_savings": None, "cut_penalty": None, "no_trade_clause": False, "incentive": None,
                     "years": years, "projected": []}
                ops.append(dict(c, op="contract_insert", player=pid))
                contracts[pid] = dict(c, id=None)
                if years[season]:
                    ops.append({"op": "cap_delta", "team_id": to, "salaries": years[season], "cap_penalty": 0})
    elif kind == "RELEASED":
        ops.append({"op": "release", "player": pid, "week": week})
        p.update(released_week=week)
        if active:                                   # his cap hit leaves; the stored cut penalty is dead money
            ops.append({"op": "contract_status", "player": pid, "status": "RELEASED"})
            ops.append({"op": "cap_delta", "team_id": active["team_id"], "salaries": -_hit(active, season),
                        "cap_penalty": active.get("cut_penalty") or 0})
            contracts.pop(pid, None)
    elif kind == "TRADED":
        ops.append({"op": "move", "player": pid, "team_id": to})
        p.update(team_id=to, released_week=None)
        if active:                                   # the same contract moves row for row (master §3.5)
            ops.append({"op": "contract_status", "player": pid, "status": "TRADED"})
            c = {k: active.get(k) for k in ("length", "years_remaining", "total_value", "bonus", "cut_savings",
                                             "cut_penalty", "no_trade_clause", "incentive", "years", "projected")}
            c["projected"] = list(c["projected"] or [])
            ops.append(dict(c, op="contract_insert", player=pid, team_id=to))
            hit = _hit(active, season)
            if hit:
                ops.append({"op": "cap_delta", "team_id": active["team_id"], "salaries": -hit, "cap_penalty": 0})
                ops.append({"op": "cap_delta", "team_id": to, "salaries": hit, "cap_penalty": 0})
            contracts[pid] = dict(c, id=None, team_id=to)
    else:                                            # RE_SIGNED / EXTENDED: this season is unchanged
        n, total = lg["years"], value
        if not active:
            report["ambiguous"].append(f"{e['heading']}: {e['text']} (no ACTIVE contract to extend)")
            return
        if not (n and total is not None):
            ops.append({"op": "contract_extend", "player": pid, "years": n, "value": total, "hits": {}})
            return
        end = season + max(1, active.get("years_remaining") or 1) - 1       # the current deal's last season
        hits = projected_hits(total, n, end + 1, state["shares"])
        ops.append({"op": "contract_extend", "player": pid, "years": n, "value": total, "hits": hits})
        active["years"] = {**(active.get("years") or {}), **hits}
        active["projected"] = sorted(set(active.get("projected") or []) | set(hits))


# ---------------------------------------------------------------- the DB ----
def read_state(cur, season, scan_seasons, apply_from=None):
    cur.execute("SELECT id, upper(name) FROM teams")
    teams = {n: i for i, n in cur.fetchall()}
    cur.execute("""SELECT p.id, p.player_name, p.full_name, s.team_id, s.position, s.released_week
                   FROM players p JOIN player_seasons s ON s.player_id = p.id AND s.season = %s""", (season,))
    players = [dict(id=i, player_name=pn, full_name=fn, team_id=t, position=po, released_week=rw, this_season=True)
               for i, pn, fn, t, po, rw in cur.fetchall()]
    cur.execute("""SELECT DISTINCT ON (p.id) p.id, p.player_name, p.full_name, s.position
                   FROM players p LEFT JOIN player_seasons s ON s.player_id = p.id
                   WHERE NOT EXISTS (SELECT 1 FROM player_seasons x WHERE x.player_id = p.id AND x.season = %s)
                   ORDER BY p.id, s.season DESC NULLS LAST""", (season,))
    players += [dict(id=i, player_name=pn, full_name=fn, team_id=None, position=po, released_week=None, this_season=False)
                for i, pn, fn, po in cur.fetchall()]
    seasons = sorted(set(scan_seasons) | {season})
    cur.execute("SELECT season, txn_date, row_key, seq FROM transactions WHERE season = ANY(%s)", (seasons,))
    existing = {tuple(r) for r in cur.fetchall()}
    cur.execute("SELECT season, txn_date, count(*) FROM transactions WHERE season = ANY(%s) GROUP BY 1, 2", (seasons,))
    group_counts = {(s, d): n for s, d, n in cur.fetchall()}
    if apply_from is None:
        cur.execute("SELECT 1 FROM transactions WHERE season = %s LIMIT 1", (season,))
        if cur.fetchone() is None:                     # first load of the season (plan §6 answer 2)
            apply_from = f"{season} SEASON - WEEK 1"
    contracts = None
    cur.execute("SELECT to_regclass('player_contracts') IS NOT NULL")
    if cur.fetchone()[0]:
        cur.execute("""SELECT id, player_id, team_id, length, years_remaining, total_value, bonus, cut_savings,
                              cut_penalty, no_trade_clause, incentive
                       FROM player_contracts WHERE season = %s AND status = 'ACTIVE'""", (season,))
        contracts = {}
        for row in cur.fetchall():
            cid, pid = row[0], row[1]
            contracts[pid] = dict(zip(("id", "player_id", "team_id", "length", "years_remaining", "total_value", "bonus",
                                       "cut_savings", "cut_penalty", "no_trade_clause", "incentive"), row), years={})
        by_id = {c["id"]: dict(c, projected=[]) for c in contracts.values()}
        contracts = {c["player_id"]: c for c in by_id.values()}
        if by_id:
            cur.execute("SELECT contract_id, year, cap_hit, projected FROM player_contract_years "
                        "WHERE contract_id = ANY(%s)", (list(by_id),))
            for cid, year, hit, proj in cur.fetchall():
                by_id[cid]["years"][year] = hit
                if proj:
                    by_id[cid]["projected"].append(year)
        shares = read_shares(cur)
    else:
        shares = {}
    return _new_state(season, teams, players, existing, group_counts, contracts, apply_from, shares)


def read_shares(cur):
    """average_shares() of the latest Season scan: ACTIVE, SCAN-sourced contracts of the newest scanned season."""
    cur.execute("""SELECT c.length, array_agg(y.cap_hit ORDER BY y.year)
                   FROM player_contracts c JOIN player_contract_years y ON y.contract_id = c.id
                   WHERE c.source = 'SCAN' AND c.status = 'ACTIVE' AND c.years_remaining = c.length
                     AND c.season = (SELECT max(season) FROM player_contracts WHERE source = 'SCAN')
                     AND y.year >= c.season AND NOT y.projected
                   GROUP BY c.id, c.length""")
    return average_shares([(n, list(h)) for n, h in cur.fetchall()])


def execute(cur, plan, season, capture_id=None, stats=None):
    """Run a plan inside the caller's transaction. Returns the inserted transactions as [(id, txn)]. stats (a
    dict) gets cap_rows_missing: teams with no team_cap_seasons row for the season (no delta written)."""
    from pipeline.contracts import insert_contract
    real, done = {}, []
    stats = stats if stats is not None else {}
    stats.setdefault("cap_rows_missing", [])

    def pid(x):
        return real.get(x, x) if x is not None else None

    for entry in plan:
        t = entry["txn"]
        for op in entry["ops"]:                       # a created player must exist before the row points at him
            if op["op"] == "create_player":
                cur.execute("INSERT INTO players (player_name, full_name) VALUES (%s, %s) RETURNING id",
                            (op["player_name"], op["full_name"]))
                real[op["tmp"]] = cur.fetchone()[0]
                cur.execute("INSERT INTO player_seasons (player_id, season, team_id, position) VALUES (%s, %s, %s, %s)",
                            (real[op["tmp"]], season, op["team_id"], op["position"]))
        cur.execute("""INSERT INTO transactions (season, week, txn_date, type, player_id, player_name, position,
                           from_team_id, to_team_id, contract_years, contract_value, raw_text, row_key, seq, parse_ok,
                           flags, applied, trade_group, capture_id)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (season, txn_date, row_key, seq) DO NOTHING RETURNING id""",
                    (t["season"], t["week"], t["txn_date"], t["type"], pid(t["player"]), t["player_name"], t["position"],
                     t["from_team_id"], t["to_team_id"], t["contract_years"], t["contract_value"], t["raw_text"],
                     t["row_key"], t["seq"], t["parse_ok"], ",".join(t["flags"]) or None, t["applied"],
                     t["trade_group"], capture_id))
        row = cur.fetchone()
        if row is None:                               # stored meanwhile: no effects
            continue
        tid = row[0]
        done.append((tid, t))
        for op in entry["ops"]:
            kind = op["op"]
            if kind == "move":
                cur.execute("""INSERT INTO player_seasons (player_id, season, team_id, position) VALUES (%s, %s, %s, %s)
                               ON CONFLICT (player_id, season) DO UPDATE SET team_id = EXCLUDED.team_id,
                               released_week = NULL""", (pid(op["player"]), season, op["team_id"], op.get("position")))
            elif kind == "release":
                cur.execute("UPDATE player_seasons SET released_week = %s WHERE player_id = %s AND season = %s",
                            (op["week"], pid(op["player"]), season))
            elif kind == "contract_status":
                cur.execute("UPDATE player_contracts SET status = %s WHERE player_id = %s AND season = %s "
                            "AND status = 'ACTIVE'", (op["status"], pid(op["player"]), season))
            elif kind == "contract_insert":
                cur.execute("UPDATE player_contracts SET status = 'SUPERSEDED' WHERE player_id = %s AND season = %s "
                            "AND status = 'ACTIVE'", (pid(op["player"]), season))
                insert_contract(cur, player_id=pid(op["player"]), team_id=op["team_id"], season=season,
                                length=op["length"], years_remaining=op["years_remaining"],
                                total_value=op["total_value"], bonus=op["bonus"], cut_savings=op["cut_savings"],
                                cut_penalty=op["cut_penalty"], no_trade_clause=op["no_trade_clause"],
                                incentive=op["incentive"], years=op["years"], source="TRANSACTION",
                                capture_id=capture_id)
                if op.get("projected"):
                    cur.execute("""UPDATE player_contract_years SET projected = true WHERE year = ANY(%s)
                                   AND contract_id = (SELECT id FROM player_contracts WHERE player_id = %s
                                                      AND season = %s AND status = 'ACTIVE')""",
                                (list(op["projected"]), pid(op["player"]), season))
            elif kind == "contract_extend":
                cur.execute("""UPDATE player_contracts SET extension_years = %s, extension_value = %s,
                                      extended_transaction_id = %s
                               WHERE player_id = %s AND season = %s AND status = 'ACTIVE' RETURNING id""",
                            (op["years"], op["value"], tid, pid(op["player"]), season))
                row = cur.fetchone()
                for year, hit in sorted(op["hits"].items()) if row else ():
                    cur.execute("""INSERT INTO player_contract_years (contract_id, year, cap_hit, projected)
                                   VALUES (%s, %s, %s, true) ON CONFLICT (contract_id, year)
                                   DO UPDATE SET cap_hit = EXCLUDED.cap_hit, projected = true""", (row[0], year, hit))
            elif kind == "cap_delta" and op["team_id"]:
                d = op["salaries"] + op["cap_penalty"]
                cur.execute("""UPDATE team_cap_seasons SET salaries = salaries + %s, cap_penalty = cap_penalty + %s,
                                      total_spend = total_spend + %s, cap_space = cap_space - %s,
                                      derived_at = now(), derived_transaction_id = %s
                               WHERE team_id = %s AND season = %s""",
                            (op["salaries"], op["cap_penalty"], d, d, tid, op["team_id"], season))
                if cur.rowcount == 0 and op["team_id"] not in stats["cap_rows_missing"]:
                    stats["cap_rows_missing"].append(op["team_id"])
    return done


def gm_hook(cur, done, report):
    """Optional step-3 hook: after a trade's legs are stored, let pipeline.gm_proposals match it to an approved
    proposal (step 3 owns the rules and the EXECUTED state; step 2 only writes gm_proposal_id)."""
    groups = {}
    for tid, t in done:
        if t["type"] == "TRADED" and t["applied"] and t["trade_group"]:
            groups.setdefault(t["trade_group"], []).append(dict(t, id=tid))
    if not groups:
        return
    cur.execute("SELECT to_regclass('gm_trade_proposals') IS NOT NULL")
    if not cur.fetchone()[0]:
        return
    try:
        from pipeline.gm_proposals import match_executed_trade
    except ImportError:
        return
    matched = {}
    for g, legs_ in groups.items():
        prop = match_executed_trade(cur, legs_)
        if prop is not None:
            cur.execute("UPDATE transactions SET gm_proposal_id = %s WHERE id = ANY(%s)", (prop, [l["id"] for l in legs_]))
            matched[g] = prop
    report["gm_proposals_matched"] = matched


def load_transactions(cur, folder, season, report, apply_from=None, capture_id=None):
    """transactions.csv in `folder` -> transactions (+ effects). Adds a 'transactions' dict to report. Raises
    TransactionsError (nothing written) on a failed check. Returns the number of new rows."""
    from pipeline.money import parse_money
    skipped = []
    entries = expand(read_rows(folder), read_meta(folder), skipped)
    years = {heading_year(e["heading"]) for e in entries} - {None}
    state = read_state(cur, season, years, apply_from)
    plan, rep = plan_effects(entries, state, parse_money)
    done = execute(cur, plan, season, capture_id, rep)
    rep["apply_from"] = state["apply_from"]
    gm_hook(cur, done, rep)
    rep["skipped_not_moves"] = skipped
    report["transactions"] = rep
    return len(done)
