"""
GM trade proposal engine (docs/team_management/03_GM_AGENTS_PLAN.md §2.4-2.5, Phases 4 and 7; PROJECT_SPEC §2.17).

Each week the trade window is open (1 <= week <= TRADE_DEADLINE_WEEK in season; the offseason window in
Phase 7) the engine: expires stale proposals, recomputes direction (at checkpoints) and needs, raises injury
triggers, works out the week's budget, searches two-team deals, checks legality and both GMs' acceptance, picks
the best, writes the rationale (template, or Gemini through engines/gm_rationale.py) and stores the proposals.
JR approves on #debug, makes the trade by hand in Madden and confirms; nothing here touches Madden (D7).

  python3 -m engines.gm_trade_engine [--season 2036] [--week 4] [--dry-run] [--replace] [--no-llm]
  python3 -m engines.gm_trade_engine --simulate-season [--approval 0.6] [--seasons 2000]
"""
import argparse
import hashlib
import itertools
import json
import math
import os
import random

from engines import trade_value as tv
from engines.gm_direction import (SLOTS, TRADE_DEADLINE_WEEK, TRADE_POS_WEIGHT, position_group, slot_ovrs,
                                  starter_slots)

# Budget (JR, 2026-10-20)
IN_SEASON_TARGET = 17
OFFSEASON_RANGE = (15, 20)               # drawn per offseason: random.Random(f"offseason:{season}")
DEADLINE_WEIGHT = 3                      # the last two weeks before the deadline weigh 3x
PRIOR_EXEC = (3, 2)                      # Beta(3, 2): p_exec starts at 0.6
MAX_NEW_PER_WEEK = 4
MAX_OPEN = 6
# Legality (sub-plan §1.1)
ROSTER_MIN, ROSTER_MAX_IN_SEASON, ROSTER_MAX_OFFSEASON = 46, 53, 65
TRADE_DEAD_MONEY = "cut_penalty"         # C4: the sending team takes a cap penalty (the PENALTY column)
CAP_MARGIN, CAP_MARGIN_DERIVED = 1_000_000, 3_000_000
RESCAN_HEADROOM = 3_000_000
FAIRNESS_SHARE, FAIRNESS_FLOOR = 0.25, 4_000_000
MIN_SLOT_GROUPS = {"QB": 1, "K": 1, "P": 1}
# Acceptance
THETA = 1_500_000                        # theta_T = $1.5M x (1 - aggressiveness)
URGENT_OVERPAY = 2_000_000               # a CONTEND team with an urgent need accepts down to -$2M x risk
SOFTMAX_TEMP = 1_000_000
# Search
MAX_ASSETS = 3
TOP_CANDIDATES = 8
TOP_TARGETS = 6
TRADE_NEED_MIN_WEIGHT = 0.5              # K and P needs are left to the CPU's signings
BUYER_FACTOR = {"CONTEND": 1.5, "RETOOL": 1.0, "REBUILD": 0.6}
SELL_AGE = 28
# Injury trigger (§2.5)
INJURY_TRIGGER_WEEKS = 4
INJURY_CLEAR_WEEKS = 2
INJURY_FIRST_P = 0.35
INJURY_EXTRA_P = 0.5
RUMOR_SHARE = 0.4
OFFSEASON_WEEK = 24                      # league week of every offseason / draft-day proposal
# The offseason window runs post-Super Bowl through the preseason over these stages; the draft stage weighs 3x.
OFFSEASON_STAGES = ("post-Super Bowl", "re-signing", "free agency", "draft", "preseason")
DRAFT_STAGE = 4
DRAFT_DAY_PICKS = 32                     # draft-day proposals: only while the GM agents make the Round 1 picks
DRAFT_DAY_MAX_P = 0.5
DRAFT_DAY_SHARE = 0.5                    # draft day gets half the draft stage's expected trades
DRAFT_DAY_PREMIUM = 1.35                 # a team trading up covets the player on the board at that pick; step 4's
                                         # board grade replaces this flat premium


# ------------------------------------------------------------------ window ----
def trades_open(week, window="IN_SEASON"):
    if window == "IN_SEASON":
        return 1 <= week <= TRADE_DEADLINE_WEEK
    return window in ("OFFSEASON", "DRAFT_DAY")


def window_for(week):
    """The trade window for league_state's week: in season through the deadline, closed from the deadline to the
    Super Bowl, the offseason from week 24 (the draft is DRAFT_DAY, run_draft_day)."""
    if 1 <= week <= TRADE_DEADLINE_WEEK:
        return "IN_SEASON"
    if week >= OFFSEASON_WEEK:
        return "OFFSEASON"
    return None


def roster_max(window):
    return ROSTER_MAX_IN_SEASON if window == "IN_SEASON" else ROSTER_MAX_OFFSEASON


def flag_value(p, key):
    """An int stored as flag 'key:<n>' (offseason stage, draft-day pick), else None."""
    for f in p.get("flags") or []:
        if f.startswith(key + ":") and f[len(key) + 1:].isdigit():
            return int(f[len(key) + 1:])
    return None


def expire_stale(open_props, week, window="IN_SEASON", stage=None, pick=None):
    """[(proposal_id, reason)] for PROPOSED/APPROVED proposals the league has moved past: their week is gone, the
    deadline passed, a later offseason stage started, or a later pick is on the clock (draft day). JR may still
    confirm an expired one he already made at the console."""
    out = []
    for p in open_props:
        if p["status"] not in ("PROPOSED", "APPROVED"):
            continue
        pw = p.get("trade_window", "IN_SEASON")
        if pw == "OFFSEASON" and stage is not None and (flag_value(p, "stage") or 0) < stage:
            out.append((p["id"], f"offseason stage {flag_value(p, 'stage')} is over"))
        elif pw == "DRAFT_DAY" and (pick is not None and (flag_value(p, "pick") or 0) < pick or
                                    window == "OFFSEASON" and (stage or 0) > DRAFT_STAGE):
            out.append((p["id"], "the pick was made"))
        elif pw in ("OFFSEASON", "DRAFT_DAY"):
            if window == "IN_SEASON":
                out.append((p["id"], "the offseason is over"))
        elif pw == "IN_SEASON" and not trades_open(week, "IN_SEASON"):
            out.append((p["id"], "trade deadline passed"))
        elif p.get("expires_week") is not None and week > p["expires_week"]:
            out.append((p["id"], f"expired after week {p['expires_week']}"))
    return out


# ------------------------------------------------------------------ budget ----
def week_shape(week):
    return DEADLINE_WEIGHT if week >= TRADE_DEADLINE_WEEK - 1 else 1


def scaled_target(target, first_week):
    """The season target scaled to the weeks the engine runs (sub-plan §7 Q2: 2036 starts at week 4)."""
    full = sum(week_shape(w) for w in range(1, TRADE_DEADLINE_WEEK + 1))
    left = sum(week_shape(w) for w in range(max(1, first_week), TRADE_DEADLINE_WEEK + 1))
    return target * left / full


def p_exec(executed, decided):
    a, b = PRIOR_EXEC
    return (a + executed) / (a + b + decided)


def poisson(rng, lam):
    if lam <= 0:
        return 0
    k, p, l = 0, 1.0, math.exp(-lam)
    while True:
        p *= rng.random()
        if p <= l:
            return k
        k += 1


def weekly_budget(season, week, executed, decided, open_count, first_week=1, target=IN_SEASON_TARGET):
    """{n, e_w, p_exec, target}: this week's new proposals. e_w = (target - executed) x shape_w / remaining shape;
    n ~ Poisson(e_w / p_exec), seeded random.Random(f"{season}:{week}"), capped at MAX_NEW_PER_WEEK and by
    MAX_OPEN open proposals."""
    tgt = scaled_target(target, first_week)
    remaining = sum(week_shape(w) for w in range(week, TRADE_DEADLINE_WEEK + 1))
    e_w = max(0.0, tgt - executed) * week_shape(week) / remaining if remaining else 0.0
    pe = p_exec(executed, decided)
    rng = random.Random(f"{season}:{week}")
    n = poisson(rng, e_w / pe if pe else 0)
    n = max(0, min(n, MAX_NEW_PER_WEEK, MAX_OPEN - open_count))
    return {"n": n, "e_w": round(e_w, 3), "p_exec": round(pe, 3), "target": round(tgt, 2), "executed": executed}


def offseason_target(season):
    return random.Random(f"offseason:{season}").randint(*OFFSEASON_RANGE)


def offseason_shape(stage):
    return DEADLINE_WEIGHT if stage == DRAFT_STAGE else 1


def offseason_budget(season, stage, executed, decided, open_count, target=None, stages=len(OFFSEASON_STAGES)):
    """The offseason uses the same pacing over its own stages (1..5, the draft stage weighted 3x); its target is
    drawn from 15-20 per offseason and draft-day trades count toward it."""
    target = target if target is not None else offseason_target(season)
    shape = offseason_shape
    remaining = sum(shape(s) for s in range(stage, stages + 1))
    e = max(0.0, target - executed) * shape(stage) / remaining if remaining else 0.0
    pe = p_exec(executed, decided)
    rng = random.Random(f"{season}:offseason:{stage}")
    n = max(0, min(poisson(rng, e / pe if pe else 0), MAX_NEW_PER_WEEK, MAX_OPEN - open_count))
    return {"n": n, "e_w": round(e, 3), "p_exec": round(pe, 3), "target": target, "executed": executed}


def simulate_season(approval=0.6, seasons=2000, first_week=1, seed=0):
    """Replay the in-season budget with JR approving (and making) each proposal with probability `approval`;
    every proposal is decided in its week. Returns the mean executed count."""
    rng = random.Random(seed)
    total = 0
    for s in range(seasons):
        executed = decided = 0
        for w in range(first_week, TRADE_DEADLINE_WEEK + 1):
            n = weekly_budget(10_000 + s, w, executed, decided, 0, first_week)["n"]
            for _ in range(n):
                decided += 1
                executed += rng.random() < approval
        total += executed
    return total / seasons


# ------------------------------------------------------------------- books ----
def read_books(cur, season, week, window="IN_SEASON", stage=None):
    """Everything the engine needs, read-only."""
    from engines.gm_direction import read_injuries
    b = {"season": season, "week": week, "window": window}
    cur.execute("SELECT id, abbreviation, city, name FROM teams WHERE abbreviation IS NOT NULL")
    b["teams"] = {r[0]: {"abbr": r[1], "city": r[2], "name": r[3]} for r in cur.fetchall()}
    cur.execute("""SELECT g.team_id, g.id, g.gm_name, g.aggressiveness, g.risk_appetite, g.pick_affinity, g.youth_bias,
                          g.position_bias, g.voice, COALESCE(st.user_controlled, false), COALESCE(st.suggestions_on, true)
                   FROM gm_profiles g LEFT JOIN gm_team_settings st ON st.team_id = g.team_id
                   WHERE g.end_season IS NULL""")
    b["gm"] = {r[0]: {"gm_id": r[1], "gm_name": r[2], "aggressiveness": float(r[3]), "risk_appetite": float(r[4]),
                      "pick_affinity": float(r[5]), "youth_bias": float(r[6]), "position_bias": r[7] or {},
                      "voice": r[8], "user_controlled": r[9], "suggestions_on": r[10]} for r in cur.fetchall()}
    cur.execute("""SELECT team_id, direction, needs, surplus, urgent_need, direction_inputs FROM gm_team_seasons
                   WHERE season = %s""", (season,))
    b["ts"] = {r[0]: {"direction": r[1], "needs": r[2] or [], "surplus": r[3] or [], "urgent_need": r[4],
                      "proj_wins": (r[5] or {}).get("proj_wins")} for r in cur.fetchall()}
    cur.execute("""SELECT team_id, cap_space, (derived_at IS NOT NULL AND derived_at > scanned_at)
                   FROM team_cap_seasons WHERE season = %s""", (season,))
    b["cap"] = {r[0]: {"cap_space": r[1], "derived": bool(r[2])} for r in cur.fetchall()}
    b["players"] = tv.read_players(cur, season)
    b["injuries"] = read_injuries(cur, season, week if window == "IN_SEASON" else 99)
    cur.execute("""SELECT a.player_id FROM gm_trade_assets a JOIN gm_trade_proposals p ON p.id = a.proposal_id
                   WHERE p.season = %s AND p.status = 'EXECUTED' AND a.asset_type = 'PLAYER'
                   UNION SELECT player_id FROM transactions WHERE season = %s AND type = 'TRADED'
                     AND player_id IS NOT NULL""", (season, season))
    b["acquired"] = {r[0] for r in cur.fetchall()}
    cur.execute("""SELECT draft_season, round, original_team_id, owner_team_id FROM draft_picks
                   WHERE NOT used AND draft_season > %s""", (season,))
    b["picks"] = {(r[0], r[1], r[2]): r[3] for r in cur.fetchall()}
    b["open"] = read_proposals(cur, season, ("PROPOSED", "APPROVED"))
    cur.execute("""SELECT count(*) FILTER (WHERE status = 'EXECUTED'),
                          count(*) FILTER (WHERE status IN ('EXECUTED', 'REJECTED')),
                          min(week) FILTER (WHERE origin = 'ENGINE')
                   FROM gm_trade_proposals WHERE season = %s AND trade_window = ANY(%s)""",
                (season, ["IN_SEASON"] if window == "IN_SEASON" else ["OFFSEASON", "DRAFT_DAY"]))
    ex, dec, first = cur.fetchone()
    b["executed"], b["decided"], b["first_week"] = ex or 0, dec or 0, min(first or week, week)
    cur.execute("""SELECT count(*) FROM gm_trade_proposals WHERE season = %s AND week = %s AND origin = 'ENGINE'
                   AND trade_window = %s AND (%s::text IS NULL OR %s = ANY(flags))""",
                (season, week, window, None if stage is None else f"stage:{stage}",
                 None if stage is None else f"stage:{stage}"))
    b["made_this_week"] = cur.fetchone()[0]
    cur.execute("SELECT deal_hash FROM gm_trade_proposals WHERE season = %s", (season,))
    b["hashes"] = {r[0] for r in cur.fetchall()}
    cur.execute("SELECT team_id, wins, losses FROM team_seasons WHERE season = %s", (season,))
    b["records"] = {r[0]: f"{r[1] or 0}-{r[2] or 0}" for r in cur.fetchall()}
    cur.execute("""SELECT ct.team_id, c.coach_name FROM coach_tenures ct JOIN coaches c ON c.id = ct.coach_id
                   WHERE ct.start_season <= %s AND (ct.end_season IS NULL OR ct.end_season >= %s)
                   ORDER BY ct.start_season""", (season, season))
    b["coaches"] = {r[0]: r[1] for r in cur.fetchall()}
    return finish_books(b)


def finish_books(b):
    """Derived parts of the books: rosters, injured sets, booked assets, team contexts."""
    b["roster"] = {}
    for p in b["players"].values():
        b["roster"].setdefault(p["team_id"], []).append(p["id"])
    inj = b.get("injuries") or {}
    b["injured"] = {pid for pid, i in inj.items() if i["is_ir"] or (i["weeks_remaining"] or 0) > 0}
    b["out"] = {pid for pid, i in inj.items() if i["is_ir"] or (i["weeks_remaining"] or 0) >= 3}
    b.setdefault("acquired", set())
    b["booked_players"], b["booked_picks"] = set(), set()
    for p in b.get("open", []):
        for a in p["assets"]:
            if a["type"] == "PLAYER":
                b["booked_players"].add(a["player_id"])
            else:
                b["booked_picks"].add(tuple(a["pick"]))
    ranks = sorted(b["teams"], key=lambda t: (b.get("ts", {}).get(t, {}).get("proj_wins") or 8.5))
    b["slots"] = {t: i + 1 for i, t in enumerate(ranks)}
    b["ctx"] = {t: team_ctx(b, t) for t in b["teams"]}
    return b


def team_ctx(b, tid):
    players = [b["players"][p] for p in b["roster"].get(tid, [])]
    slots = starter_slots(players, b["out"])
    lines = {}
    for g, n in SLOTS.items():
        lines[g] = min((p["ovr"] or 0) for p in slots[g]) if len(slots[g]) == n else None
    gm = b["gm"].get(tid, {})
    ts = b.get("ts", {}).get(tid, {})
    return {"team_id": tid, "direction": ts.get("direction") or "RETOOL", "needs": ts.get("needs") or [],
            "surplus": ts.get("surplus") or [], "urgent_need": ts.get("urgent_need"),
            "starter_ids": {p["id"] for g in SLOTS for p in slots[g]}, "lines": lines,
            "position_bias": gm.get("position_bias") or {}, "youth_bias": gm.get("youth_bias", 0.0),
            "pick_affinity": gm.get("pick_affinity", 1.0), "slots": b["slots"],
            "aggressiveness": gm.get("aggressiveness", 0.5), "risk_appetite": gm.get("risk_appetite", 0.5)}


def read_proposals(cur, season, statuses=None, ids=None):
    """Proposals with their assets: [{id, status, week, expires_week, trade_window, team_a_id, team_b_id, ...,
    assets: [{type, player_id | pick, from, to, contract_id, cap_hit_now}]}]."""
    where, args = ["p.season = %s"], [season]
    if statuses:
        where.append("p.status = ANY(%s)")
        args.append(list(statuses))
    if ids:
        where.append("p.id = ANY(%s)")
        args.append(list(ids))
    cur.execute(f"""SELECT p.id, p.status, p.week, p.expires_week, p.trade_window, p.team_a_id, p.team_b_id,
                           p.origin, p.trigger_kind, p.deal_hash, p.rumor_ok, p.flags, p.headline, p.rationale_a,
                           p.rationale_b, p.rationale_source, p.legality, p.value_a, p.value_b, p.league_value_a,
                           p.league_value_b, p.cap_space_before_a, p.cap_space_before_b, p.cap_space_after_a,
                           p.cap_space_after_b, p.dead_money_a, p.dead_money_b, p.roster_after_a, p.roster_after_b,
                           p.cap_derived_a, p.cap_derived_b, p.status_reason, p.executed_as, p.created_at,
                           p.approved_at, p.executed_at, p.season
                    FROM gm_trade_proposals p WHERE {' AND '.join(where)} ORDER BY p.id""", args)
    cols = ("id", "status", "week", "expires_week", "trade_window", "team_a_id", "team_b_id", "origin",
            "trigger_kind", "deal_hash", "rumor_ok", "flags", "headline", "rationale_a", "rationale_b",
            "rationale_source", "legality", "value_a", "value_b", "league_value_a", "league_value_b",
            "cap_space_before_a", "cap_space_before_b", "cap_space_after_a", "cap_space_after_b", "dead_money_a",
            "dead_money_b", "roster_after_a", "roster_after_b", "cap_derived_a", "cap_derived_b", "status_reason",
            "executed_as", "created_at", "approved_at", "executed_at", "season")
    props = [dict(zip(cols, r), assets=[]) for r in cur.fetchall()]
    by = {p["id"]: p for p in props}
    if by:
        cur.execute("""SELECT proposal_id, asset_type, player_id, contract_id, pick_draft_season, pick_round,
                              pick_original_team_id, from_team_id, to_team_id, cap_hit_now, value_from, value_to,
                              value_league
                       FROM gm_trade_assets WHERE proposal_id = ANY(%s) ORDER BY id""", (list(by),))
        for r in cur.fetchall():
            a = {"type": r[1], "from": r[7], "to": r[8], "cap_hit_now": r[9], "value_from": r[10], "value_to": r[11],
                 "value_league": r[12]}
            if r[1] == "PLAYER":
                a.update(player_id=r[2], contract_id=r[3])
            else:
                a["pick"] = (r[4], r[5], r[6])
            by[r[0]]["assets"].append(a)
    return props


# ------------------------------------------------------------------ values ----
class Valuer:
    """Cached asset values: value(asset, team) and league(asset)."""

    def __init__(self, books, fits):
        self.b, self.fits, self.cache = books, fits, {}

    def value(self, asset, tid):
        key = (asset_key(asset), tid)
        if key not in self.cache:
            season = self.b["season"]
            ctx = self.b["ctx"][tid]
            if asset["type"] == "PLAYER":
                p = dict(self.b["players"][asset["player_id"]])
                self.cache[key] = tv.player_value(p, ctx, self.fits, season)["value"]
            else:
                ds, rd, orig = asset["pick"]
                v = tv.pick_value({"draft_season": ds, "round": rd, "original_team_id": orig}, ctx, self.fits, season)
                clock = self.b.get("on_clock")
                if clock and tuple(asset["pick"]) == clock[0] and tid != clock[1]:
                    v = round(v * DRAFT_DAY_PREMIUM)
                self.cache[key] = v
        return self.cache[key]

    def dead(self, asset, tid):
        """The sender's dead money for a player, x its this-season discount."""
        if asset["type"] != "PLAYER":
            return 0
        d0 = tv.DISCOUNTS[self.b["ctx"][tid]["direction"]][0]
        return round(dead_money(self.b["players"][asset["player_id"]]) * d0)

    def league(self, asset):
        key = (asset_key(asset), None)
        if key not in self.cache:
            season = self.b["season"]
            if asset["type"] == "PLAYER":
                self.cache[key] = tv.league_value(self.b["players"][asset["player_id"]], self.fits, season)
            else:
                ds, rd, orig = asset["pick"]
                self.cache[key] = tv.pick_value({"draft_season": ds, "round": rd, "original_team_id": orig}, None,
                                                self.fits, season)
        return self.cache[key]


def asset_key(a):
    return ("P", a["player_id"]) if a["type"] == "PLAYER" else ("K",) + tuple(a["pick"])


def deal_hash(team_a, team_b, assets):
    parts = sorted(f"{asset_key(a)}:{a['from']}>{a['to']}" for a in assets)
    return hashlib.sha1(f"{min(team_a, team_b)}|{max(team_a, team_b)}|{'|'.join(parts)}".encode()).hexdigest()[:16]


def deltas(deal, valuer):
    """(Δ_A, Δ_B, league value A receives, league value B receives). The sender of a player also pays his dead
    money this season (C4), discounted like any this-season dollar for its direction."""
    a, b = deal["a"], deal["b"]
    da = db = la = lb = 0
    for x in deal["assets"]:
        if x["to"] == a:
            da += valuer.value(x, a)
            db -= valuer.value(x, b) + valuer.dead(x, b)
            la += valuer.league(x)
        else:
            db += valuer.value(x, b)
            da -= valuer.value(x, a) + valuer.dead(x, a)
            lb += valuer.league(x)
    return da, db, la, lb


# --------------------------------------------------------------- legality ----
def cap_hit_now(p, season):
    c = p.get("contract") or {}
    return ((c.get("years") or {}).get(season, (0, False))[0]) or 0


def dead_money(p):
    if TRADE_DEAD_MONEY != "cut_penalty":
        return 0
    return (p.get("contract") or {}).get("cut_penalty") or 0


def cap_effects(deal, b):
    """{team_id: {before, after, dead, derived, incoming, outgoing}} for this season."""
    out = {}
    season = b["season"]
    for t in (deal["a"], deal["b"]):
        cap = b["cap"].get(t) or {}
        before = cap.get("cap_space")
        inc = sum(cap_hit_now(b["players"][x["player_id"]], season) for x in deal["assets"]
                  if x["type"] == "PLAYER" and x["to"] == t)
        outg = sum(cap_hit_now(b["players"][x["player_id"]], season) for x in deal["assets"]
                   if x["type"] == "PLAYER" and x["from"] == t)
        dead = sum(dead_money(b["players"][x["player_id"]]) for x in deal["assets"]
                   if x["type"] == "PLAYER" and x["from"] == t)
        after = None if before is None else before - inc + outg - dead
        out[t] = {"before": before, "after": after, "dead": dead, "derived": bool(cap.get("derived")),
                  "incoming": inc, "outgoing": outg}
    return out


def roster_after(deal, b):
    out = {}
    for t in (deal["a"], deal["b"]):
        n = len(b["roster"].get(t, []))
        n += sum(1 for x in deal["assets"] if x["type"] == "PLAYER" and x["to"] == t)
        n -= sum(1 for x in deal["assets"] if x["type"] == "PLAYER" and x["from"] == t)
        out[t] = n
    return out


def check_legality(deal, b, valuer=None, ignore_booking=()):
    """{check: {ok, detail}} for cap, ntc, roster, slots, deadline, picks, booking, tradeable and fairness (fairness
    only with a valuer). ignore_booking: asset keys booked by this very proposal (a re-check)."""
    season, window = b["season"], b["window"]
    res = {}
    caps = cap_effects(deal, b)
    bad = []
    for t, c in caps.items():
        margin = CAP_MARGIN_DERIVED if c["derived"] else CAP_MARGIN
        if c["after"] is None:
            bad.append(f"{abbr(b, t)}: no cap row")
        elif c["after"] < margin and c["after"] < (c["before"] or 0):
            bad.append(f"{abbr(b, t)}: ${c['after'] / 1e6:.2f}M after (margin ${margin / 1e6:.0f}M)")
    res["cap"] = {"ok": not bad, "detail": "; ".join(bad) or "fits"}
    ntc = [b["players"][x["player_id"]]["name"] for x in deal["assets"] if x["type"] == "PLAYER"
           and (b["players"][x["player_id"]].get("contract") or {}).get("no_trade_clause")]
    res["ntc"] = {"ok": not ntc, "detail": ", ".join(ntc) or "none"}
    rost = roster_after(deal, b)
    bad = []
    mx = roster_max(window)
    for t, n in rost.items():
        before = len(b["roster"].get(t, []))
        if n > mx and n > before:
            bad.append(f"{abbr(b, t)} {n} > {mx}")
        if n < ROSTER_MIN and n < before:
            bad.append(f"{abbr(b, t)} {n} < {ROSTER_MIN}")
    res["roster"] = {"ok": not bad, "detail": "; ".join(bad) or ", ".join(f"{abbr(b, t)} {n}" for t, n in rost.items())}
    bad = []
    for t in (deal["a"], deal["b"]):
        for g, need in MIN_SLOT_GROUPS.items():
            have = sum(1 for pid in b["roster"].get(t, []) if b["players"][pid]["group"] == g)
            have += sum(1 for x in deal["assets"] if x["type"] == "PLAYER" and x["to"] == t
                        and b["players"][x["player_id"]]["group"] == g)
            have -= sum(1 for x in deal["assets"] if x["type"] == "PLAYER" and x["from"] == t
                        and b["players"][x["player_id"]]["group"] == g)
            if have < need:
                bad.append(f"{abbr(b, t)} no {g}")
    res["slots"] = {"ok": not bad, "detail": "; ".join(bad) or "ok"}
    res["deadline"] = {"ok": trades_open(b["week"], window), "detail": f"week {b['week']} ({window})"}
    bad = []
    for x in deal["assets"]:
        if x["type"] == "PICK":
            owner = b["picks"].get(tuple(x["pick"]))
            if owner != x["from"]:
                bad.append(f"{pick_label(b, x['pick'])} not owned by {abbr(b, x['from'])}")
    res["picks"] = {"ok": not bad, "detail": "; ".join(bad) or "owned"}
    booked = [asset_label(b, x) for x in deal["assets"] if asset_key(x) not in ignore_booking and (
        (x["type"] == "PLAYER" and x["player_id"] in b["booked_players"]) or
        (x["type"] == "PICK" and tuple(x["pick"]) in b["booked_picks"]))]
    res["booking"] = {"ok": not booked, "detail": ", ".join(booked) or "free"}
    bad = []
    for x in deal["assets"]:
        if x["type"] == "PLAYER":
            p = b["players"].get(x["player_id"])
            if p is None or p["team_id"] != x["from"]:
                bad.append(f"player {x['player_id']} not on {abbr(b, x['from'])}")
                continue
            ok, why = tv.tradeable(p, b["injured"], b["acquired"])
            if not ok and why != "no-trade clause":
                bad.append(f"{p['name']}: {why}")
    res["tradeable"] = {"ok": not bad, "detail": "; ".join(bad) or "ok"}
    if valuer is not None:
        _, _, la, lb = deltas(deal, valuer)
        gap, limit = abs(la - lb), max(FAIRNESS_SHARE * max(abs(la), abs(lb)), FAIRNESS_FLOOR)
        res["fairness"] = {"ok": gap <= limit,
                           "detail": f"league value ${la / 1e6:.1f}M vs ${lb / 1e6:.1f}M (gap ${gap / 1e6:.1f}M, "
                                     f"limit ${limit / 1e6:.1f}M)"}
    return res


def legal(checks):
    return all(c["ok"] for c in checks.values())


# -------------------------------------------------------------- acceptance ----
def threshold(ctx, urgent=False):
    if urgent and ctx["direction"] == "CONTEND":
        return -URGENT_OVERPAY * ctx["risk_appetite"]
    return THETA * (1 - ctx["aggressiveness"])


def accepts(deal, valuer, b):
    """(both, Δ_A, Δ_B): each GM's surplus must clear θ_T; a CONTEND team with an urgent need in a group it
    receives may overpay down to -$2M x risk_appetite."""
    da, db, _, _ = deltas(deal, valuer)
    res = []
    for t, d in ((deal["a"], da), (deal["b"], db)):
        ctx = b["ctx"][t]
        urgent = ctx.get("urgent_need") and any(
            x["type"] == "PLAYER" and x["to"] == t and b["players"][x["player_id"]]["group"] == ctx["urgent_need"].get("group")
            for x in deal["assets"])
        res.append(d >= threshold(ctx, bool(urgent)))
    return all(res), da, db


def choose(cands, rng):
    """Seeded softmax over the joint surplus min(Δ_A, Δ_B) of the best candidates."""
    if not cands:
        return None
    cands = sorted(cands, key=lambda c: -c["joint"])[:5]
    top = cands[0]["joint"]
    ws = [math.exp((c["joint"] - top) / SOFTMAX_TEMP) for c in cands]
    r = rng.random() * sum(ws)
    for c, w in zip(cands, ws):
        r -= w
        if r <= 0:
            return c
    return cands[-1]


# ------------------------------------------------------------------ search ----
def eligible_team(b, tid):
    gm = b["gm"].get(tid)
    return gm is not None and (not gm["user_controlled"] or gm["suggestions_on"])


def free_player(b, pid):
    p = b["players"].get(pid)
    return (p is not None and pid not in b["booked_players"]
            and tv.tradeable(p, b["injured"], b["acquired"])[0])


def package_assets(b, tid, exclude_groups=()):
    """A team's tradeable non-starters and owned picks: [{type, ...}] (from = tid, to filled later)."""
    ctx = b["ctx"][tid]
    out = []
    for pid in b["roster"].get(tid, []):
        p = b["players"][pid]
        if pid in ctx["starter_ids"] or p["group"] in exclude_groups or p["group"] in ("K", "P"):
            continue
        if free_player(b, pid):
            out.append({"type": "PLAYER", "player_id": pid, "from": tid})
    for key, owner in b["picks"].items():
        if owner == tid and key not in b["booked_picks"]:
            out.append({"type": "PICK", "pick": key, "from": tid})
    return out


def trade_needs(ctx, k=2):
    return [n for n in ctx["needs"] if TRADE_POS_WEIGHT.get(n["group"], 0) >= TRADE_NEED_MIN_WEIGHT][:k]


def buy_deals(b, valuer, buyer, groups=None, trigger="NEED"):
    """Deals where `buyer` gets a starter for one of its top needs from a team that values him less."""
    ctx = b["ctx"][buyer]
    groups = groups or [n["group"] for n in trade_needs(ctx)]
    deals = []
    for g in groups:
        line = ctx["lines"].get(g)
        targets = []
        for pid, p in b["players"].items():
            if p["group"] != g or p["team_id"] == buyer or not eligible_team(b, p["team_id"]):
                continue
            if line is not None and (p["ovr"] or 0) <= line:
                continue
            if not free_player(b, pid):
                continue
            x = {"type": "PLAYER", "player_id": pid, "from": p["team_id"], "to": buyer}
            gain = valuer.value(x, buyer) - valuer.value(x, p["team_id"])
            if gain > 0:
                targets.append((gain, x))
        targets.sort(key=lambda t: -t[0])
        for _, target in targets[:TOP_TARGETS]:
            deals += packages(b, valuer, buyer, target["from"], [target], trigger)
    return deals


def sell_deals(b, valuer, seller):
    """A REBUILD team's veterans (28+, starters) offered to teams that need the group and would start them."""
    deals = []
    ctx = b["ctx"][seller]
    vets = [b["players"][pid] for pid in b["roster"].get(seller, []) if pid in ctx["starter_ids"]
            and (b["players"][pid]["age"] or 0) >= SELL_AGE and free_player(b, pid)
            and TRADE_POS_WEIGHT.get(b["players"][pid]["group"], 0) >= TRADE_NEED_MIN_WEIGHT]
    scored = []
    for p in vets:
        for buyer in b["teams"]:
            if buyer == seller or not eligible_team(b, buyer):
                continue
            bctx = b["ctx"][buyer]
            if bctx["direction"] == "REBUILD" or p["group"] not in [n["group"] for n in trade_needs(bctx, 3)]:
                continue
            x = {"type": "PLAYER", "player_id": p["id"], "from": seller, "to": buyer}
            gain = valuer.value(x, buyer) - valuer.value(x, seller)
            if gain > 0:
                scored.append((gain, buyer, x))
    scored.sort(key=lambda s: -s[0])
    for _, buyer, x in scored[:TOP_TARGETS]:
        for d in packages(b, valuer, buyer, seller, [x], "SELL"):
            d["a"], d["b"] = seller, buyer             # the seller initiated
            deals.append(d)
    return deals


def packages(b, valuer, buyer, seller, core, trigger, extras=True):
    """Every package of up to MAX_ASSETS from the buyer's top candidates (valued by the seller) against the core,
    plus up to MAX_ASSETS - len(core) extras from the seller's non-starters (valued by the buyer)."""
    give = package_assets(b, buyer)
    for x in give:
        x["to"] = seller
    give.sort(key=lambda x: -valuer.value(x, seller))
    give = give[:TOP_CANDIDATES]
    core_keys = {asset_key(x) for x in core}
    extra = [dict(x, to=buyer) for x in package_assets(b, seller) if asset_key(x) not in core_keys]
    extra.sort(key=lambda x: -valuer.value(x, buyer))
    extra = extra[:TOP_CANDIDATES - 1] if extras else []
    deals = []
    room = MAX_ASSETS - len(core)
    for k in range(1, MAX_ASSETS + 1):
        for g in itertools.combinations(give, k):
            for j in range(0, room + 1):
                for e in itertools.combinations(extra, j):
                    deals.append({"a": buyer, "b": seller, "assets": list(core) + list(e) + list(g),
                                  "trigger": trigger})
    return deals


def evaluate(deals, b, valuer):
    """Candidates that are legal (fairness included) and accepted by both GMs, with their numbers."""
    out = []
    for d in deals:
        h = deal_hash(d["a"], d["b"], d["assets"])
        if h in b["hashes"]:
            continue
        ok, da, db = accepts(d, valuer, b)
        if not ok:
            continue
        checks = check_legality(d, b, valuer)
        if not legal(checks):
            continue
        out.append(dict(d, hash=h, da=da, db=db, joint=min(da, db), legality=checks))
    return out


# ---------------------------------------------------------------- triggers ----
def injury_triggers(b):
    """{team_id: urgent_need or None} for every team whose state changes. Fires for a CONTEND team's starter by
    slot (full lineup) in a group weighted >= 1.0 or at QB, on IR or out >= 4 weeks; clears when he's back under
    2 weeks or the slot is filled (his group is no longer a need)."""
    out = {}
    inj = b.get("injuries") or {}
    for tid in b["teams"]:
        ctx = b["ctx"][tid]
        cur = ctx.get("urgent_need")
        players = [b["players"][p] for p in b["roster"].get(tid, [])]
        full = starter_slots(players, ())
        fire = None
        if ctx["direction"] == "CONTEND":
            for g in SLOTS:
                if TRADE_POS_WEIGHT[g] < 1.0 and g != "QB":
                    continue
                for p in full[g]:
                    i = inj.get(p["id"])
                    if i and (i["is_ir"] or (i["weeks_remaining"] or 0) >= INJURY_TRIGGER_WEEKS):
                        cand = {"group": g, "player_id": p["id"], "weeks_remaining": i["weeks_remaining"],
                                "is_ir": i["is_ir"]}
                        if fire is None or TRADE_POS_WEIGHT[g] > TRADE_POS_WEIGHT[fire["group"]]:
                            fire = cand
        if fire:
            if cur != fire:
                out[tid] = fire
        elif cur:
            i = inj.get(cur.get("player_id"))
            back = not i or (not i["is_ir"] and (i["weeks_remaining"] or 0) < INJURY_CLEAR_WEEKS)
            filled = cur.get("group") not in [n["group"] for n in ctx["needs"]]
            if back or filled or ctx["direction"] != "CONTEND":
                out[tid] = None
    return out


def initiator_order(b, rng, exclude=()):
    """Initiators drawn (without replacement) with weight need score x aggressiveness x direction factor;
    urgent-need teams go first with probability 0.35 x (0.5 + aggressiveness) x premium."""
    first, pool = [], []
    for tid in b["teams"]:
        if tid in exclude or not eligible_team(b, tid):
            continue
        ctx = b["ctx"][tid]
        u = ctx.get("urgent_need")
        if u and rng.random() < INJURY_FIRST_P * (0.5 + ctx["aggressiveness"]) * TRADE_POS_WEIGHT.get(u["group"], 1):
            first.append(tid)
            continue
        need = sum(n["score"] for n in trade_needs(ctx))
        w = need * ctx["aggressiveness"] * BUYER_FACTOR.get(ctx["direction"], 1.0)
        if ctx["direction"] == "REBUILD":
            w = max(w, 5.0 * ctx["aggressiveness"])           # REBUILD teams also sell
        if w > 0:
            pool.append((w, tid))
    order = list(first)
    while pool:
        r = rng.random() * sum(w for w, _ in pool)
        for i, (w, t) in enumerate(pool):
            r -= w
            if r <= 0:
                order.append(t)
                pool.pop(i)
                break
        else:
            order.append(pool.pop()[1])
    return order, first


# ------------------------------------------------------------------- text ----
def abbr(b, tid):
    return b["teams"].get(tid, {}).get("abbr", str(tid))


def team_name(b, tid):
    t = b["teams"].get(tid, {})
    return f"{t.get('city', '')} {t.get('name', '')}".strip() or str(tid)


def pick_label(b, pick):
    ds, rd, orig = pick
    return f"{ds} R{rd} ({abbr(b, orig)})"


def asset_label(b, x):
    if x["type"] == "PICK":
        return pick_label(b, x["pick"])
    p = b["players"].get(x["player_id"]) or {}
    return f"{p.get('pos', '?')} {p.get('name', x['player_id'])} {p.get('ovr', '')}".strip()


def steps_text(b, deal):
    """The steps JR follows in Madden for an approved proposal (§2.7)."""
    a, bt = deal["a"], deal["b"]
    ua = b["gm"].get(a, {}).get("user_controlled")
    ub = b["gm"].get(bt, {}).get("user_controlled")
    send = [asset_label(b, x) for x in deal["assets"] if x["from"] == a]
    get = [asset_label(b, x) for x in deal["assets"] if x["from"] == bt]
    s1 = (f"Options > User Teams: you already control the {team_name(b, a)}." if ua else
          f"Options > User Teams: switch the {team_name(b, a)} to user control.")
    steps = [s1,
             f"Trade with the {team_name(b, bt)}. Send: {', '.join(send)}. Receive: {', '.join(get)}.",
             f"Submit the offer, then switch to the {team_name(b, bt)} to accept it" +
             ("." if ub else " (switch them to user control for this, or turn CPU trades on just for this trade)."),
             "If Madden refuses, click Couldn't make it.",
             "Switch any team you switched back to CPU (and CPU trades back off).",
             "Click Made it."]
    return steps


def template_rationale(b, deal):
    """Headline and each side's two sentences, from the deal's facts only."""
    from engines.gm_rationale import template
    return template(fact_sheet(b, deal))


def fact_sheet(b, deal):
    from engines.gm_rationale import build_fact_sheet
    return build_fact_sheet(b, deal)


# ------------------------------------------------------------------- store ----
def store(cur, b, deal, valuer, rationale, flags=(), origin="ENGINE", status="PROPOSED", trigger=None,
          executed_as=None):
    """Insert the proposal and its assets. Returns the id (None when the deal_hash exists)."""
    season, week, window = b["season"], b["week"], b["window"]
    caps = cap_effects(deal, b)
    rost = roster_after(deal, b)
    da, db, la, lb = deltas(deal, valuer) if valuer else (None, None, None, None)
    a, bt = deal["a"], deal["b"]
    h = deal.get("hash") or deal_hash(a, bt, deal["assets"])
    rumor = random.Random(f"rumor:{season}:{h}").random() < RUMOR_SHARE
    cur.execute("""INSERT INTO gm_trade_proposals (season, week, trade_window, trigger_kind, origin, expires_week,
                       team_a_id, team_b_id, value_a, value_b, league_value_a, league_value_b,
                       cap_space_before_a, cap_space_before_b, cap_space_after_a, cap_space_after_b,
                       dead_money_a, dead_money_b, roster_after_a, roster_after_b, cap_derived_a, cap_derived_b,
                       legality, rationale_a, rationale_b, headline, rationale_source, status, rumor_ok, flags,
                       deal_hash, executed_as, executed_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                           %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CASE WHEN %s = 'EXECUTED' THEN now() END)
                   ON CONFLICT (season, deal_hash) DO NOTHING RETURNING id""",
                (season, week, window, trigger or deal.get("trigger", "NEED"), origin, week, a, bt, da, db, la, lb,
                 caps[a]["before"], caps[bt]["before"], caps[a]["after"], caps[bt]["after"], caps[a]["dead"],
                 caps[bt]["dead"], rost[a], rost[bt], caps[a]["derived"], caps[bt]["derived"],
                 json.dumps(deal.get("legality") or {}), rationale.get("rationale_a"), rationale.get("rationale_b"),
                 rationale.get("headline"), rationale.get("source", "TEMPLATE"), status, rumor, list(flags), h,
                 executed_as, status))
    row = cur.fetchone()
    if not row:
        return None
    pid = row[0]
    for x in deal["assets"]:
        p = b["players"].get(x.get("player_id")) if x["type"] == "PLAYER" else None
        pick = x.get("pick") or (None, None, None)
        cur.execute("""INSERT INTO gm_trade_assets (proposal_id, from_team_id, to_team_id, asset_type, player_id,
                           contract_id, pick_draft_season, pick_round, pick_original_team_id, cap_hit_now, value_from,
                           value_to, value_league)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (pid, x["from"], x["to"], x["type"], x.get("player_id"),
                     ((p or {}).get("contract") or {}).get("contract_id"), pick[0], pick[1], pick[2],
                     cap_hit_now(p, season) if p else None,
                     valuer.value(x, x["from"]) if valuer else None, valuer.value(x, x["to"]) if valuer else None,
                     valuer.league(x) if valuer else None))
    return pid


def set_status(cur, pid, status, reason=None):
    cur.execute("UPDATE gm_trade_proposals SET status = %s, status_reason = %s WHERE id = %s", (status, reason, pid))


# --------------------------------------------------------------------- run ----
def run_week(cur, season, week, dry_run=False, replace=False, llm=None, log=print, window=None, stage=None):
    """One engine run. Returns a report dict; in a dry run nothing is written (the caller rolls back too). In the
    offseason (week 24) `stage` is the offseason stage 1-5 (OFFSEASON_STAGES; default 1)."""
    from engines import gm_direction
    window = window or window_for(week)
    if window == "OFFSEASON":
        stage = stage or 1
    rep = {"season": season, "week": week, "window": window, "expired": [], "proposals": [], "budget": None,
           "triggers": {}, "rechecked": []}
    if window is None:
        log(f"trade window closed (week {week}, deadline {TRADE_DEADLINE_WEEK})")
        if not dry_run:
            for pid, why in expire_stale(read_proposals(cur, season, ("PROPOSED", "APPROVED")), week):
                set_status(cur, pid, "EXPIRED", why)
                rep["expired"].append((pid, why))
        return rep
    if replace and not dry_run:
        cur.execute("""DELETE FROM gm_trade_proposals WHERE season = %s AND week = %s AND origin = 'ENGINE'
                       AND status = 'PROPOSED' AND trade_window = %s
                       AND (%s::text IS NULL OR %s = ANY(flags))""",
                    (season, week, window, None if stage is None else f"stage:{stage}",
                     None if stage is None else f"stage:{stage}"))
        log(f"replaced: {cur.rowcount} of this week's PROPOSED proposals removed")
    # 1. expire stale
    open_props = read_proposals(cur, season, ("PROPOSED", "APPROVED"))
    for pid, why in expire_stale(open_props, week, window, stage):
        rep["expired"].append((pid, why))
        if not dry_run:
            set_status(cur, pid, "EXPIRED", why)
    # 2. direction + needs (written unless dry run; the books read what the run computed)
    dir_week = week if window == "IN_SEASON" else 24
    rows = gm_direction.compute(gm_direction.read_league(cur, season, min(dir_week, 23)), dir_week)
    if not dry_run:
        gm_direction.write(cur, season, dir_week, rows)
    b = read_books(cur, season, week, window, stage)
    for tid, r in rows.items():                          # a dry run never wrote them: use them in memory
        ts = b["ts"].setdefault(tid, {})
        ts.update(direction=r["direction"], needs=r["needs"], surplus=r["surplus"], proj_wins=r["inputs"]["proj_wins"])
    expired_ids = {pid for pid, _ in rep["expired"]}
    b["open"] = [p for p in b["open"] if p["id"] not in expired_ids]
    b = finish_books(b)
    fits = tv.fits_from_db(cur, season)
    valuer = Valuer(b, fits)
    # re-check what's still open
    for p in list(b["open"]):
        deal = {"a": p["team_a_id"], "b": p["team_b_id"], "assets": p["assets"]}
        checks = check_legality(deal, b, None, ignore_booking={asset_key(x) for x in p["assets"]})
        checks.pop("deadline", None)
        if not legal(checks):
            why = "no longer legal: " + "; ".join(f"{k} {v['detail']}" for k, v in checks.items() if not v["ok"])
            rep["rechecked"].append((p["id"], why))
            if not dry_run:
                set_status(cur, p["id"], "EXPIRED", why)
    # 3. injury triggers
    trig = injury_triggers(b)
    rep["triggers"] = trig
    for tid, u in trig.items():
        b["ts"].setdefault(tid, {})["urgent_need"] = u
        b["ctx"][tid]["urgent_need"] = u
        if not dry_run:
            cur.execute("UPDATE gm_team_seasons SET urgent_need = %s WHERE team_id = %s AND season = %s",
                        (json.dumps(u) if u else None, tid, season))
    # 4. budget
    open_count = len(b["open"]) - len(rep["rechecked"])
    if window == "IN_SEASON":
        bud = weekly_budget(season, week, b["executed"], b["decided"], open_count, b["first_week"])
    else:
        bud = offseason_budget(season, stage, b["executed"], b["decided"], open_count)
        bud["stage"] = f"{stage} {OFFSEASON_STAGES[stage - 1]}"
    n = max(0, bud["n"] - b["made_this_week"])
    rng = random.Random(f"{season}:{week}:{window}:search")
    order, urgent_first = initiator_order(b, rng)
    if n == 0 and urgent_first and rng.random() < INJURY_EXTRA_P and open_count < MAX_OPEN:
        n, bud["injury_extra"] = 1, True
    rep["budget"] = dict(bud, new=n, made_before=b["made_this_week"])
    log(f"budget: target {bud['target']} executed {bud['executed']} e_w {bud['e_w']} p_exec {bud['p_exec']} "
        f"-> {n} new (open {open_count}, cap {MAX_NEW_PER_WEEK}/week, {MAX_OPEN} open)")
    # 5-8. search, legality, acceptance, choice
    used = set()
    for tid in order:
        if len(rep["proposals"]) >= n:
            break
        if tid in used:
            continue
        ctx = b["ctx"][tid]
        u = ctx.get("urgent_need")
        if tid in urgent_first and u:
            deals, trigger = buy_deals(b, valuer, tid, [u["group"]], "INJURY"), "INJURY"
        elif ctx["direction"] == "REBUILD" and rng.random() < 0.5:
            deals = sell_deals(b, valuer, tid)
        else:
            deals = buy_deals(b, valuer, tid)
        deals = [d for d in deals if d["b"] not in used and d["a"] not in used]
        cands = evaluate(deals, b, valuer)
        pick = choose(cands, rng)
        if not pick:
            continue
        used.update((pick["a"], pick["b"]))
        flags = [f"advisory:{abbr(b, t)}" for t in (pick["a"], pick["b"]) if b["gm"].get(t, {}).get("user_controlled")]
        caps = cap_effects(pick, b)
        flags += [f"rescan:{abbr(b, t)}" for t, c in caps.items()
                  if c["derived"] and c["after"] is not None and c["after"] < RESCAN_HEADROOM]
        if window == "OFFSEASON":
            flags.append(f"stage:{stage}")
        rat = rationale_for(b, pick, llm)
        pid = None if dry_run else store(cur, b, pick, valuer, rat, flags)
        for x in pick["assets"]:                          # booked for the rest of this run
            if x["type"] == "PLAYER":
                b["booked_players"].add(x["player_id"])
            else:
                b["booked_picks"].add(tuple(x["pick"]))
        b["hashes"].add(pick["hash"])
        rep["proposals"].append({"id": pid, "deal": pick, "rationale": rat, "flags": flags,
                                 "steps": steps_text(b, pick), "caps": caps})
        print_proposal(b, pick, rat, caps, flags, log)
    rep["books"] = b
    return rep


def draft_day_p(season, executed, decided, open_count):
    """Chance that a Round 1 pick on the clock draws a trade-up proposal: DRAFT_DAY_SHARE of the draft stage's
    expected trades over the 32 picks, divided by the approval rate, capped at 0.5."""
    bud = offseason_budget(season, DRAFT_STAGE, executed, decided, open_count)
    pe = bud["p_exec"] or 1
    return min(DRAFT_DAY_MAX_P, DRAFT_DAY_SHARE * bud["e_w"] / (pe * DRAFT_DAY_PICKS))


def run_draft_day(cur, season, pick, dry_run=False, llm=None, log=print):
    """Draft-day trades (step 4 calls this for each of the first 32 picks, live): the pick on the clock may draw
    one pick-for-pick trade-up proposal from a team that values it more than its owner does. pick: {draft_season,
    round, original_team_id, overall_pick}. Earlier picks' open draft-day proposals expire. Returns a report."""
    overall = pick["overall_pick"]
    rep = {"season": season, "pick": overall, "expired": [], "proposals": [], "p": None}
    open_props = read_proposals(cur, season, ("PROPOSED", "APPROVED"))
    for pid, why in expire_stale(open_props, OFFSEASON_WEEK, "DRAFT_DAY", pick=overall):
        rep["expired"].append((pid, why))
        if not dry_run:
            set_status(cur, pid, "EXPIRED", why)
    if overall > DRAFT_DAY_PICKS:
        return rep
    b = read_books(cur, season, OFFSEASON_WEEK, "DRAFT_DAY")
    expired = {pid for pid, _ in rep["expired"]}
    b["open"] = [p for p in b["open"] if p["id"] not in expired]
    b = finish_books(b)
    key = (pick["draft_season"], pick["round"], pick["original_team_id"])
    owner = b["picks"].get(key)
    if owner is None or not eligible_team(b, owner):
        return rep
    b["slots"][pick["original_team_id"]] = overall - (pick["round"] - 1) * 32   # the real slot, not a projection
    b["on_clock"] = (key, owner)
    p = draft_day_p(season, b["executed"], b["decided"], len(b["open"]))
    rep["p"] = round(p, 3)
    rng = random.Random(f"{season}:draft:{overall}")
    if rng.random() >= p or len(b["open"]) >= MAX_OPEN:
        return rep
    valuer = Valuer(b, tv.fits_from_db(cur, season))
    core = {"type": "PICK", "pick": key, "from": owner}
    deals = []
    for buyer in b["teams"]:
        if buyer == owner or not eligible_team(b, buyer):
            continue
        x = dict(core, to=buyer)
        if valuer.value(x, buyer) <= valuer.value(x, owner):
            continue
        deals += [d for d in packages(b, valuer, buyer, owner, [x], "DRAFT", extras=False)
                  if all(a["type"] == "PICK" for a in d["assets"])]
    cands = evaluate(deals, b, valuer)
    best = choose(cands, rng)
    if not best:
        return rep
    flags = [f"pick:{overall}"] + [f"advisory:{abbr(b, t)}" for t in (best["a"], best["b"])
                                   if b["gm"].get(t, {}).get("user_controlled")]
    rat = rationale_for(b, best, llm)
    pid = None if dry_run else store(cur, b, best, valuer, rat, flags, trigger="DRAFT")
    rep["proposals"].append({"id": pid, "deal": best, "rationale": rat, "flags": flags, "steps": steps_text(b, best)})
    print_proposal(b, best, rat, cap_effects(best, b), flags, log)
    return rep


def rationale_for(b, deal, llm):
    from engines.gm_rationale import league_names, write_rationale
    if llm is not None and "league_names" not in b:
        b["league_names"] = league_names(b)
    return write_rationale(fact_sheet(b, deal), llm, b.get("league_names"))


def print_proposal(b, d, rat, caps, flags, log=print):
    log(f"\n  [{d.get('trigger')}] {abbr(b, d['a'])} <-> {abbr(b, d['b'])}: {rat.get('headline')}")
    for t in (d["a"], d["b"]):
        gets = [asset_label(b, x) for x in d["assets"] if x["to"] == t]
        c = caps[t]
        after = "?" if c["after"] is None else f"${c['after'] / 1e6:.2f}M"
        log(f"    {abbr(b, t)} gets {', '.join(gets)}; cap ${(c['before'] or 0) / 1e6:.2f}M -> {after}"
            f"{' (dead $%.2fM)' % (c['dead'] / 1e6) if c['dead'] else ''}")
    log(f"    surplus {abbr(b, d['a'])} ${d['da'] / 1e6:+.2f}M, {abbr(b, d['b'])} ${d['db'] / 1e6:+.2f}M; "
        f"{d['legality'].get('fairness', {}).get('detail', '')}")
    if flags:
        log(f"    flags: {', '.join(flags)}")
    log(f"    A: {rat.get('rationale_a')}")
    log(f"    B: {rat.get('rationale_b')}")
    for i, s in enumerate(steps_text(b, d), 1):
        log(f"    {i}. {s}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season", type=int)
    ap.add_argument("--week", type=int)
    ap.add_argument("--dry-run", action="store_true", help="read-only: print the proposals, write nothing")
    ap.add_argument("--replace", action="store_true", help="remove this week's PROPOSED proposals first")
    ap.add_argument("--no-llm", action="store_true", help="template rationale only")
    ap.add_argument("--stage", type=int, choices=range(1, len(OFFSEASON_STAGES) + 1),
                    help="offseason stage 1-5 (with --week 24): " + ", ".join(OFFSEASON_STAGES))
    ap.add_argument("--draft-pick", type=int, metavar="OVERALL",
                    help="draft day: the Round 1 pick on the clock (needs draft_picks.overall_pick set)")
    ap.add_argument("--simulate-season", action="store_true")
    ap.add_argument("--approval", type=float, default=0.6)
    ap.add_argument("--seasons", type=int, default=2000)
    ap.add_argument("--first-week", type=int, default=1)
    a = ap.parse_args(argv)
    if a.simulate_season:
        m = simulate_season(a.approval, a.seasons, a.first_week)
        print(f"in-season target {IN_SEASON_TARGET} (scaled {scaled_target(IN_SEASON_TARGET, a.first_week):.1f} from "
              f"week {a.first_week}), JR approval {a.approval}: mean executed {m:.2f} over {a.seasons} seasons")
        return
    import psycopg
    from engines.gm_llm import flush, make_llm
    llm = None if (a.dry_run or a.no_llm) else make_llm("gm_rationale")
    dsn = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")
    with psycopg.connect(dsn) as conn:
        cur = conn.cursor()
        cur.execute("SELECT current_season, current_week FROM league_state WHERE id = 1")
        s, w = cur.fetchone()
        season, week = a.season or s, a.week or w
        if a.draft_pick:
            cur.execute("""SELECT draft_season, round, original_team_id FROM draft_picks
                           WHERE draft_season = %s AND overall_pick = %s""", (season + 1, a.draft_pick))
            row = cur.fetchone()
            if not row:
                raise SystemExit(f"no draft_picks row with overall_pick {a.draft_pick} for {season + 1}")
            rep = run_draft_day(cur, season, {"draft_season": row[0], "round": row[1], "original_team_id": row[2],
                                              "overall_pick": a.draft_pick}, dry_run=a.dry_run, llm=llm)
            print(f"\npick {a.draft_pick}: p {rep['p']}, {len(rep['proposals'])} proposals, {len(rep['expired'])} expired")
        else:
            rep = run_week(cur, season, week, dry_run=a.dry_run, replace=a.replace, llm=llm, stage=a.stage)
            print(f"\nweek {week}: {len(rep['proposals'])} proposals, {len(rep['expired'])} expired, "
                  f"{len(rep['rechecked'])} no longer legal")
        if a.dry_run:
            conn.rollback()
            print("dry run: nothing written")
        else:
            conn.commit()
    flush(llm)


if __name__ == "__main__":
    main()
