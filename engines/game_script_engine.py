"""Game Script engine (PROJECT_SPEC §2.14): one canonical story per completed game, shared by every writer.

The box score says what happened; the script decides how. For each completed game it builds quarter scores (the
captured ones when the box score has them), the scoring plays in order with scorer, yardage, try, drive and game
clock, the lead changes, the score that won it, key non-scoring moments (takeaways, sacks, missed kicks, fourth
downs, big plays), halftime adjustments and defensive standouts. Everything the database records is honoured
(final score, quarter scores, each player's touchdowns and longest plays, kicking, defensive lines, turnovers,
two-point and fourth-down results where real); everything it doesn't record is invented, seeded by game id so a
rerun gives the same story. How dramatic the story is follows the game's Excitement Index: of a few hundred
orderings that fit the data, the one at the game's excitement percentile is kept.

Scripts are stored in `game_scripts` (one row per game) and only rebuilt when the game's recorded facts change
(`facts_hash`) or with --force, which skips scripts that something published after them may cite unless
--unlock-published is given, so a story published once stays the same story.

    python3 -m engines.game_script_engine [--season N] [--week N] [--game ID] [--force [--unlock-published]] [--print]
"""
import argparse
import hashlib
import json
import os
import random

import psycopg

SCRIPT_VERSION = 1
SAMPLES = 240
PERIOD_NAMES = ("Q1", "Q2", "Q3", "Q4", "OT")
PERIOD_WORDS = ("first quarter", "second quarter", "third quarter", "fourth quarter", "overtime")
MIN_GAP = 75                                        # seconds between two scores (a kickoff and at least a few snaps)
PERIOD_SECONDS = (900, 900, 900, 900, 600)
PERIOD_WEIGHTS = (0.22, 0.30, 0.20, 0.28)          # share of NFL scoring by quarter (2nd and 4th are busiest)
DEF_POSITIONS = ("DT", "LEDG", "REDG", "MIKE", "WILL", "SAM", "CB", "FS", "SS")
EXCITEMENT_FLOOR, EXCITEMENT_SPAN = 35.0, 45.0     # raw E 35 → tamest ordering, 80+ → most dramatic
EXCITEMENT_CURVE = 1.6   # random orderings swing more than real games: this curve gives ~15-17% 10-point comebacks

DATABASE_URL = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")


# ------------------------------------------------------------------------------------------------- helpers ---
def _clock(seconds: int) -> str:
    return f"{seconds // 60}:{seconds % 60:02d}"


def _other(side: str) -> str:
    return "home" if side == "away" else "away"


def _n(v) -> int:
    return int(v or 0)


def excitement_quantile(raw) -> float:
    if raw is None:
        return 0.5
    return min(1.0, max(0.0, (float(raw) - EXCITEMENT_FLOOR) / EXCITEMENT_SPAN)) ** EXCITEMENT_CURVE


def solve_points(score, n_td, fgm=None, xpm=None, two_pt=None, safeties=None, extra_td_ok=False) -> list:
    """Ways to make `score` from the recorded touchdowns, cheapest (most ordinary) first. Each is a dict:
    tds, xp (kicks made), two (two-point tries made), safeties, fgs, extra_td (touchdowns no line records:
    only when the game's defense and returns weren't captured), dropped_td (recorded touchdowns that can't fit,
    only for synthetic lines)."""
    out = []
    for e in range(3 if extra_td_ok else 1):
        for d in range(n_td + 1):
            n = n_td + e - d
            for x in ([xpm] if xpm is not None else range(n + 1)):
                if x > n:
                    continue
                for c in ([two_pt] if two_pt is not None else range(n - x + 1)):
                    if x + c > n:
                        continue
                    for s in ([safeties] if safeties is not None else (0, 1)):
                        rem = score - 6 * n - x - 2 * c - 2 * s
                        if rem < 0 or rem % 3:
                            continue
                        f = rem // 3
                        if fgm is not None and f != fgm:
                            continue
                        cost = (10 * d + 4 * e + 2.5 * (n - x - c) + 1.5 * c + 6 * s
                                + 0.5 * max(0, f - 3) + 1.0 * max(0, f - 4))
                        out.append((cost, {"tds": n, "xp": x, "two": c, "safeties": s, "fgs": f,
                                           "extra_td": e, "dropped_td": d}))
    out.sort(key=lambda t: t[0])
    return [o for _, o in out]


# ------------------------------------------------------------------------------------------ scoring units ---
def _td_sources(team: dict, opp: dict, rng: random.Random) -> list:
    """Every touchdown the lines record for this team, as unit dicts (no try yet)."""
    tds = []
    for p in team["offense"]:
        tds += [{"type": "rush", "scorer": p} for _ in range(_n(p.get("rush_tds")))]
    passers = [p for p in team["offense"] for _ in range(_n(p.get("pass_tds")))]
    catchers = [p for p in team["offense"] for _ in range(_n(p.get("rec_tds")))]
    rng.shuffle(passers)
    rng.shuffle(catchers)
    for i in range(max(len(passers), len(catchers))):
        tds.append({"type": "pass", "passer": passers[i] if i < len(passers) else None,
                    "scorer": catchers[i] if i < len(catchers) else None})
    for d in team["defense"] or []:
        for k in range(_n(d.get("tds"))):
            tds.append({"type": "int_return" if k < _n(d.get("ints")) else "fumble_return", "scorer": d})
    for r in team["returns"]:
        tds += [{"type": r["kind"] + "_return", "scorer": r} for _ in range(_n(r.get("tds")))]
    return tds


def _build_units(side: str, team: dict, opp: dict, rng: random.Random) -> tuple:
    """(units, tries, plan): touchdowns, field goals and safeties that make the team's score, and the multiset of
    try results (1 kick, 2 two-point, 0 missed/failed) for its touchdowns."""
    tds = _td_sources(team, opp, rng)
    kicking = team["kicking"]
    defense_captured = team["defense"] is not None
    safeties = sum(_n(d.get("safeties")) for d in (team["defense"] or [])) if defense_captured else None
    known = dict(fgm=kicking["fgm"] if kicking else None, xpm=kicking["xpm"] if kicking else None,
                 two_pt=team.get("two_pt_made"), safeties=safeties)
    plans = solve_points(team["score"], len(tds), extra_td_ok=not defense_captured, **known)
    if not plans:   # recorded numbers that can't all be true together: keep the score, loosen the rest
        plans = solve_points(team["score"], len(tds), extra_td_ok=True)
    plan = plans[0] if plans else {"tds": 0, "xp": 0, "two": 0, "safeties": 0, "fgs": 0, "extra_td": 0, "dropped_td": 0}
    if plan["dropped_td"]:
        rng.shuffle(tds)
        tds = tds[plan["dropped_td"]:]
    tds += [{"type": "defense_unknown", "scorer": None} for _ in range(plan["extra_td"])]
    units = [{"side": side, "kind": "td", "base": 6, **t} for t in tds]
    units += [{"side": side, "kind": "fg", "base": 3} for _ in range(plan["fgs"])]
    units += [{"side": side, "kind": "safety", "base": 2} for _ in range(plan["safeties"])]
    tries = [1] * plan["xp"] + [2] * plan["two"] + [0] * (plan["tds"] - plan["xp"] - plan["two"])
    return units, tries, plan, plans


def _partitions(units: list, tries: list, targets: list, rng: random.Random, attempts: int = 30) -> list:
    """Assignments {unit index: (period, try)} whose per-period points equal the captured quarter scores."""
    found, seen = [], set()
    for _ in range(attempts):
        order = list(range(len(units)))
        rng.shuffle(order)
        order.sort(key=lambda i: -units[i]["base"])
        assign, failed = {}, set()

        def bt(k, remaining, left):
            if k == len(order):
                return all(r == 0 for r in remaining)
            key = (k, tuple(remaining), tuple(sorted(left)))
            if key in failed:
                return False
            u = units[order[k]]
            periods = [p for p in range(5) if remaining[p] > 0]
            rng.shuffle(periods)
            options = sorted(set(left)) if u["kind"] == "td" else [None]
            rng.shuffle(options)
            for p in periods:
                for t in options:
                    v = u["base"] + (t or 0)
                    if remaining[p] < v:
                        continue
                    remaining[p] -= v
                    nxt = list(left)
                    if t is not None:
                        nxt.remove(t)
                    assign[order[k]] = (p, t)
                    if bt(k + 1, remaining, nxt):
                        return True
                    remaining[p] += v
                    del assign[order[k]]
            failed.add(key)
            return False

        if bt(0, list(targets), list(tries)):
            sig = tuple(sorted(assign.items()))
            if sig not in seen:
                seen.add(sig)
                found.append(dict(assign))
        elif not found:
            return []   # the search is exhaustive: one failure means no assignment fits
    return found


# ------------------------------------------------------------------------------------------------ timeline ---
def _flow(events: list, winner) -> dict:
    """Lead changes, ties, largest leads, the winner's biggest deficit and the score that won it."""
    score = {"away": 0, "home": 0}
    leader, lead_changes, ties = None, 0, 0
    largest = {"away": (0, None), "home": (0, None)}
    winner_deficit, deficit_at = 0, None
    last_not_winning = -1
    halftime = regulation = None
    for i, ev in enumerate(events):
        if halftime is None and ev["period"] >= 2:
            halftime = dict(score)
        if regulation is None and ev["period"] >= 4:
            regulation = dict(score)
        score[ev["side"]] += ev["points"]
        ev["score_after"] = dict(score)
        diff = score["away"] - score["home"]
        now = "away" if diff > 0 else "home" if diff < 0 else None
        if now is None:
            ties += 1
        if now and leader and now != leader:
            lead_changes += 1
        if now:
            leader = now
        for s in ("away", "home"):
            lead = score[s] - score[_other(s)]
            if lead > largest[s][0]:
                largest[s] = (lead, i)
        if winner:
            w_lead = score[winner] - score[_other(winner)]
            if -w_lead > winner_deficit:
                winner_deficit, deficit_at = -w_lead, i
            if w_lead <= 0:
                last_not_winning = i
    if halftime is None:
        halftime = dict(score)
    if regulation is None:
        regulation = dict(score)
    return {"lead_changes": lead_changes, "ties": ties, "largest": largest, "winner_deficit": winner_deficit,
            "deficit_at": deficit_at, "decisive": (last_not_winning + 1) if winner and last_not_winning + 1 < len(events) else None,
            "halftime": halftime, "regulation": regulation}


def _drama(events: list, flow: dict, winner, overtime: bool) -> float:
    d = 2.0 * flow["lead_changes"] + 0.8 * flow["ties"] + min(flow["winner_deficit"], 21) / 3.5
    if flow["decisive"] is not None:
        ev = events[flow["decisive"]]
        if ev["period"] == 4:
            d += 4
        elif ev["period"] == 3 and ev["clock"] <= 300:
            d += 4
        elif ev["period"] == 3:
            d += 2
    if overtime:
        d += 3
    if winner:
        d -= flow["largest"][winner][0] / 10.0
    return d


def _sample_events(units: dict, tries: dict, partitions: list, game: dict, rng: random.Random) -> tuple:
    """One ordering of both teams' scoring units: (events, overtime)."""
    events = []
    margin = game["away"]["score"] - game["home"]["score"]
    winner = "away" if margin > 0 else "home" if margin < 0 else None
    overtime = False
    if partitions:
        assign = rng.choice(partitions)
        for side in ("away", "home"):
            for i, u in enumerate(units[side]):
                p, t = assign[side][i]
                events.append({**u, "period": p, "try": t})
        overtime = game["overtime_known"]
    else:
        for side in ("away", "home"):
            left = list(tries[side])
            rng.shuffle(left)
            for u in units[side]:
                t = left.pop() if u["kind"] == "td" else None
                p = rng.choices(range(4), weights=PERIOD_WEIGHTS)[0]
                events.append({**u, "period": p, "try": t})
        if winner is None:
            overtime = game["tie_possible_ot"]
        elif abs(margin) in (3, 6):
            # a winner's lone field goal (margin 3) or try-less touchdown (margin 6) can be the overtime winner
            cands = [e for e in events if e["side"] == winner and e["base"] + (e["try"] or 0) == abs(margin)]
            if not cands and abs(margin) == 6:
                zero = [e for e in events if e["side"] == winner and e["kind"] == "td" and e["try"] == 0]
                cands = zero
            if cands and rng.random() < 0.25:
                pick = rng.choice(cands)
                pick["period"] = 4
                overtime = True
    for p in range(5):
        in_p = [e for e in events if e["period"] == p]
        top, k = PERIOD_SECONDS[p], len(in_p)
        gap = min(MIN_GAP, (top - 40) // max(1, k))
        # k clocks at least `gap` apart: sample from a shortened range, then spread them out
        base = sorted(rng.sample(range(8, top - 25 - gap * (k - 1)), k)) if k else []
        clocks = sorted((c + i * gap for i, c in enumerate(base)), reverse=True)
        rng.shuffle(in_p)
        for e, c in zip(in_p, clocks):
            e["clock"] = c
    if overtime and winner:   # the overtime winner is the last score of the game
        ot = [e for e in events if e["period"] == 4]
        win_ot = [e for e in ot if e["side"] == winner]
        if win_ot:
            last = min(ot, key=lambda e: e["clock"])
            if last["side"] != winner:
                w = win_ot[0]
                w["clock"], last["clock"] = last["clock"], w["clock"]
    events.sort(key=lambda e: (e["period"], -e["clock"]))
    for e in events:
        e["points"] = e["base"] + (e["try"] or 0)
    return events, overtime


# ----------------------------------------------------------------------------------------------- narration ---
def _name(p) -> str:
    return (p or {}).get("name") or "an unnamed player"


def _play_yards(rng, budget, longest=None, short_bias=True, long_chance=0.0):
    """Touchdown play length within the player's recorded yards and longest play."""
    cap = max(1, min(budget if budget is not None else 60, longest if longest else 99))
    if longest and longest <= cap and rng.random() < long_chance:
        return longest
    roll = rng.random()
    if short_bias and roll < 0.55:
        return rng.randint(1, min(cap, 9))
    if roll < 0.85:
        return rng.randint(1, min(cap, 24))
    return rng.randint(1, cap)


class _Budget:
    """Per-player yards left for touchdown plays, so no player's TD plays outgrow his line."""

    def __init__(self):
        self.used = {}

    def take(self, key, total):
        if total is None:
            return None
        return max(1, int(total) - self.used.get(key, 0))

    def spend(self, key, y):
        self.used[key] = self.used.get(key, 0) + y


def _describe(events, game, rng) -> None:
    budget = _Budget()
    long_used = set()
    kicker = {s: game[s]["kicking"]["name"] if game[s]["kicking"] else game[s].get("kicker") for s in ("away", "home")}
    fg_long_used = set()
    xp_misses = {}
    for s in ("away", "home"):
        k = game[s]["kicking"]
        xp_misses[s] = max(0, _n(k["xpa"]) - _n(k["xpm"])) if k else None
    for i, e in enumerate(events):
        side, team = e["side"], game[e["side"]]
        if e["kind"] == "td":
            t, p = e["type"], e.get("scorer")
            if t == "rush":
                key = ("rush", _name(p))
                y = _play_yards(rng, budget.take(key, p.get("rush_yards")), p.get("rush_long") if key not in long_used else None,
                                long_chance=0.35 if (p.get("rush_long") or 0) >= 15 else 0.0)
                if y == p.get("rush_long"):
                    long_used.add(key)
                budget.spend(key, y)
                e["yards"], e["play"] = y, f"{_name(p)} {y}-yard touchdown run"
            elif t == "pass":
                q, key = e.get("passer"), ("rec", _name(p))
                longest = p.get("rec_long") if p and key not in long_used else None
                y = _play_yards(rng, budget.take(key, p.get("rec_yards")) if p else None, longest, short_bias=False,
                                long_chance=0.6 if (p and (p.get("rec_long") or 0) >= 40) else 0.25)
                if p and y == p.get("rec_long"):
                    long_used.add(key)
                budget.spend(key, y)
                e["yards"] = y
                if q and p:
                    e["play"] = f"{q['name']} {y}-yard touchdown pass to {p['name']}"
                elif p:
                    e["play"] = f"{p['name']} {y}-yard touchdown catch"
                else:
                    e["play"] = f"{_name(q)} {y}-yard touchdown pass"
            elif t == "int_return":
                y = p.get("int_long") or rng.randint(12, 70)
                victims = [o for o in game[_other(side)]["offense"] if _n(o.get("ints"))]
                e["yards"] = y
                e["play"] = f"{_name(p)} {y}-yard interception return touchdown" + (f" off {victims[0]['name']}" if victims else "")
            elif t == "fumble_return":
                y = rng.randint(5, 60)
                e["yards"], e["play"] = y, f"{_name(p)} {y}-yard fumble return touchdown"
            elif t in ("kick_return", "punt_return"):
                y = p.get("long") or rng.randint(60, 100)
                e["yards"] = y
                e["play"] = f"{_name(p)} {y}-yard {'kickoff' if t == 'kick_return' else 'punt'} return touchdown"
            else:
                y = rng.randint(8, 55)
                e["yards"], e["play"] = y, f"{y}-yard fumble return touchdown by the defense"
            e["play"] += _try_text(e, events, i, team, kicker[side], xp_misses, rng)
        elif e["kind"] == "fg":
            k = team["kicking"]
            longest = k.get("fg_long") if k else None
            if longest and side not in fg_long_used:
                d = longest
                fg_long_used.add(side)
            else:
                d = rng.randint(20, longest) if longest and longest > 20 else rng.choices(
                    [rng.randint(22, 34), rng.randint(35, 44), rng.randint(45, 55)], weights=(4, 4, 2))[0]
            e["distance"] = d
            e["play"] = f"{kicker[side] or 'The kicker'} {d}-yard field goal"
        else:
            who = [d for d in (team["defense"] or []) if _n(d.get("safeties"))]
            e["play"] = "Safety" + (f": {who[0]['name']} brought the ball carrier down in the end zone" if who else "")


def _try_text(e, events, i, team, kicker, xp_misses, rng) -> str:
    t = e["try"]
    if t == 1:
        return f" ({kicker} kick)" if kicker else " (kick good)"
    if t == 2:
        qb = max(team["offense"], key=lambda o: _n(o.get("pass_yards")), default=None)
        catchers = [o for o in team["offense"] if _n(o.get("receptions")) > 0]
        runners = [o for o in team["offense"] if _n(o.get("rush_yards")) > 0 and o is not qb]
        if qb and _n(qb.get("pass_yards")) and catchers and (not runners or rng.random() < 0.6):
            return f" (two-point try good: {qb['name']} pass to {rng.choice(catchers)['name']})"
        if runners:
            return f" (two-point try good: {rng.choice(runners)['name']} run)"
        return " (two-point try good)"
    if e["period"] == 4 and i == len(events) - 1:
        return " (game over, no try)"
    side = e["side"]
    if xp_misses.get(side):
        xp_misses[side] -= 1
        return f" ({kicker} kick no good)" if kicker else " (kick no good)"
    if xp_misses.get(side) == 0:
        return " (two-point try failed)"
    return " (two-point try failed)" if e["period"] >= 3 and rng.random() < 0.6 else " (kick no good)"


def _drives(events, game, rng) -> None:
    """Invented drives for offensive scores, inside the clock gaps between scores and the team's recorded yards."""
    prev_clock, prev_side = {}, {}
    for e in events:
        p = e["period"]
        if p in prev_clock:
            start = prev_clock[p]
        elif p in (1, 3):   # a drive can carry over from the 1st into the 2nd quarter, or the 3rd into the 4th
            start = PERIOD_SECONDS[p] + min(prev_clock.get(p - 1, PERIOD_SECONDS[p - 1]), 360)
        else:
            start = PERIOD_SECONDS[p]
        avail = start - e["clock"] - 12
        if prev_side.get(p) == e["side"]:
            avail -= 45   # the other team had the ball in between
        prev_clock[p], prev_side[p] = e["clock"], e["side"]
        offensive = e["kind"] == "fg" or (e["kind"] == "td" and e["type"] in ("rush", "pass"))
        if not offensive or avail < 15:
            continue
        play = e.get("yards", 0) if e["kind"] == "td" else 0
        spot = (e.get("distance", 35) - 17) if e["kind"] == "fg" else 0
        lo, hi = (18, 48) if e.get("short_field") else (50, 85)
        lo = min(99, max(play, spot + 1, lo))
        start_from_goal = rng.randint(lo, max(lo, hi))
        yards = max(play, start_from_goal - spot)
        if yards == play and e["kind"] == "td":
            plays = 1
        else:
            plays = max(2, round(yards / rng.uniform(5.5, 10.5)))
        secs = min(avail, int(plays * rng.uniform(22, 36)))
        if plays > max(1, secs // 15):
            # not enough clock for the drive drawn: fewer plays from a shorter field (at most ~11 yards a snap)
            plays = max(1, secs // 15)
            yards = max(play, min(yards, play + (plays - 1) * 11)) if e["kind"] == "td" else min(yards, plays * 11)
        e["drive_secs"] = max(secs, 8)
        e["drive"] = {"plays": plays, "yards": yards, "time": _clock(e["drive_secs"])}
    for side in ("away", "home"):
        cap = game[side].get("total_yards")
        if not cap:
            continue
        drives = [e for e in events if e["side"] == side and e.get("drive")]
        total = sum(e["drive"]["yards"] for e in drives)
        if total > 0.9 * cap:
            k = 0.9 * cap / total
            for e in drives:
                d = e["drive"]
                floor = e.get("yards", 0) if e["kind"] == "td" else 0
                d["yards"] = max(floor, int(d["yards"] * k))
                d["plays"] = max(1, min(d["plays"], round(d["yards"] / 4) or 1))


def _score_text(score, game) -> str:
    a, h = score["away"], score["home"]
    if a == h:
        return f"Tied {a}-{a}"
    lead = "away" if a > h else "home"
    return f"{game[lead]['abbr']} {max(a, h)}-{min(a, h)}"


def _moments(events, game, flow, winner, overtime, rng) -> list:
    """Non-scoring moments that the data supports: takeaways, sacks, missed kicks, fourth downs, big plays."""
    moments = []
    scoring_by_side = {s: [e for e in events if e["side"] == s and (e["kind"] == "fg" or e.get("type") in ("rush", "pass"))]
                       for s in ("away", "home")}

    for side in ("away", "home"):
        team, opp = game[side], game[_other(side)]
        # takeaways by `side`: the other offense's interceptions and lost fumbles
        picks_thrown = [(o, k) for o in opp["offense"] for k in range(_n(o.get("ints")))]
        pick_sixes = sum(1 for e in events if e["side"] == side and e.get("type") == "int_return")
        picks_thrown = picks_thrown[pick_sixes:]
        interceptors = [d for d in (team["defense"] or []) for _ in range(_n(d.get("ints")))]
        interceptors = interceptors[pick_sixes:]
        fumbles = opp.get("fumbles_lost")
        if fumbles is None:
            fumbles = sum(_n(o.get("fumbles")) for o in opp["offense"])
        fumble_returns = sum(1 for e in events if e["side"] == side and e.get("type") in ("fumble_return", "defense_unknown"))
        recoverers = [d for d in (team["defense"] or []) for _ in range(_n(d.get("fr")))]
        forcers = [d for d in (team["defense"] or []) for _ in range(_n(d.get("ff")))]
        takeaways = [("int", q, interceptors[i] if i < len(interceptors) else None) for i, (q, _) in enumerate(picks_thrown)]
        takeaways += [("fumble", None, forcers[i] if i < len(forcers) else (recoverers[i] if i < len(recoverers) else None))
                      for i in range(max(0, fumbles - fumble_returns))]
        open_scores = list(scoring_by_side[side])
        rng.shuffle(open_scores)
        for kind, qb, d in takeaways:
            target = None
            if open_scores and rng.random() < 0.55:
                target = open_scores.pop()
                target["short_field"] = True
                target["set_up_by"] = "takeaway"
            if kind == "int":
                text = (f"{d['name']} intercepted {qb['name']}" if d else f"{qb['name']} was intercepted") + (
                    "" if d else " (interceptor not recorded)")
            else:
                text = (f"{d['name']} forced a fumble, recovered by {team['abbr']}" if d
                        else f"{team['abbr']} recovered a fumble")
            moments.append({"side": side, "type": "takeaway", "text": text, "before": target})
        # sacks (captured defensive lines only)
        sackers = sorted((d for d in (team["defense"] or []) if (d.get("sacks") or 0) >= 1), key=lambda d: -d["sacks"])
        if sackers:
            d = sackers[0]
            dec = events[flow["decisive"]] if winner == side and flow["decisive"] is not None else None
            window = (3, 10, min(170, dec["clock"] - 25)) if dec and dec["period"] == 3 and dec["clock"] > 40 else None
            text = (f"{d['name']} sack on a late third down helped close it out" if window
                    else f"{d['name']} sack stalled a drive")
            total = d["sacks"]
            text += f" ({total:g} sack{'s' if total != 1 else ''} on the day)"
            moments.append({"side": side, "type": "sack", "text": text, "window": window})
        # missed field goals (captured kicking only)
        k = team["kicking"]
        if k and _n(k.get("fga")) > _n(k.get("fgm")):
            for _ in range(_n(k["fga"]) - _n(k["fgm"])):
                d = rng.randint(38, 56)
                moments.append({"side": side, "type": "missed_fg", "text": f"{k['name']} missed from {d} yards"})
        # fourth downs (only when the box score's numbers are real)
        fd = team.get("fourth_down")
        if fd:
            conv, att = fd
            for _ in range(max(0, att - conv)):
                moments.append({"side": _other(side), "type": "fourth_down_stop",
                                "text": f"{opp['abbr']} stopped {team['abbr']} on fourth down"})
            drives = [e for e in scoring_by_side[side] if e["kind"] == "td"]
            for _ in range(min(conv, len(drives))):
                e = rng.choice(drives)
                e["fourth_down_conversion"] = True
        # big non-scoring plays (captured longest plays that weren't touchdowns)
        td_yards = {e.get("yards") for e in events if e["side"] == side and e["kind"] == "td"}
        big = sorted(((longest, o["name"], kind) for o in team["offense"]
                      for kind, longest in (("catch", o.get("rec_long")), ("run", o.get("rush_long")))
                      if longest and longest >= 30 and longest not in td_yards), reverse=True)[:2]
        for longest, name, kind in big:
            moments.append({"side": side, "type": "big_play", "text": f"{name} {longest}-yard {kind}",
                            "big_play_yards": longest})
    return moments


def _time_moments(moments, events, overtime, rng) -> list:
    """Clock times for the moments, after the drives exist: a takeaway that set up a score comes just before that
    drive, a big play sits inside one of its team's scoring drives or between drives, and nothing else lands inside
    a scoring drive."""
    windows = [(e["period"], e["clock"], e["clock"] + e["drive_secs"]) for e in events if e.get("drive_secs")]
    scores = [(e["period"], e["clock"]) for e in events]

    def free(p, c):
        return (not any(p == wp and lo - 10 <= c <= hi + 10 for wp, lo, hi in windows)
                and not any(p == sp and abs(c - sc) < 20 for sp, sc in scores))

    def pick(window=None):
        for _ in range(40):
            if window:
                p, c = window[0], rng.randint(window[1], max(window[1], window[2]))
            else:
                p, c = rng.choices(range(4), weights=PERIOD_WEIGHTS)[0], rng.randint(20, 880)
            if free(p, c):
                return p, c
        return p, c

    out = []
    for m in moments:
        target = m.pop("before", None)
        yards = m.pop("big_play_yards", None)
        if target is not None:
            p = target["period"]
            c = min(PERIOD_SECONDS[p] - 5, target["clock"] + target.get("drive_secs", 0) + rng.randint(15, 60))
        elif yards:
            hosts = [e for e in events if e["side"] == m["side"] and e.get("drive") and e["drive"]["yards"] - (
                e.get("yards", 0) if e["kind"] == "td" else 0) >= yards and e["drive"]["plays"] >= 2]
            if hosts and rng.random() < 0.6:
                e = rng.choice(hosts)
                p, c = e["period"], min(PERIOD_SECONDS[e["period"]] - 5, e["clock"] + rng.randint(15, max(16, e["drive_secs"] - 5)))
                m["text"] += " to set up a score"
            else:
                p, c = pick()
        else:
            p, c = pick(m.pop("window", None))
        m.pop("window", None)
        out.append({**m, "period": p, "clock": c, "period_name": PERIOD_NAMES[min(p, 4 if overtime else 3)]})
    out.sort(key=lambda m: (m["period"], -m["clock"]))
    return out


ADJUST_DEFENSE = {
    "man_high": ["played more press-man on the outside", "trusted the corners in man and sent an extra rusher"],
    "man_low": ["rolled to two-high safety looks and kept everything in front", "dropped into deeper zone shells"],
    "3-4": ["moved to more odd fronts and stunted the edge rushers", "walked a linebacker down as an extra run defender"],
    "4-3": ["simplified the front and let the four-man rush win", "slanted the line to clog the run lanes"],
    "any": ["brought more pressure off the edge", "spied the quarterback", "took away the quick game underneath"],
}


def _adjustments(events, game, flow, winner, rng) -> list:
    halves = {s: [0, 0] for s in ("away", "home")}
    for e in events:
        halves[e["side"]][0 if e["period"] < 2 else 1] += e["points"]
    out = []
    for side in ("away", "home"):
        opp = _other(side)
        team = game[side]
        coach = team.get("coach") or {}
        if halves[opp][0] >= 14 and halves[opp][1] <= 7 or halves[opp][0] >= 17 and halves[opp][1] <= 10:
            pool = list(ADJUST_DEFENSE["any"])
            if coach.get("man_coverage") == "High":
                pool += ADJUST_DEFENSE["man_high"]
            elif coach.get("man_coverage") == "Low":
                pool += ADJUST_DEFENSE["man_low"]
            if coach.get("defensive_scheme") in ADJUST_DEFENSE:
                pool += ADJUST_DEFENSE[coach["defensive_scheme"]]
            who = f"{coach['name']}'s defense" if coach.get("name") else f"The {team['name']} defense"
            out.append({"side": side, "unit": "defense", "text": (
                f"{who} {rng.choice(pool)} after halftime: {game[opp]['abbr']} scored {halves[opp][0]} in the first "
                f"half and {halves[opp][1]} after it")})
        if halves[side][0] <= 7 and halves[side][1] >= 14:
            rusher = max(team["offense"], key=lambda o: _n(o.get("rush_yards")), default=None)
            catcher = max(team["offense"], key=lambda o: _n(o.get("rec_yards")), default=None)
            qb = max(team["offense"], key=lambda o: _n(o.get("pass_yards")), default=None)
            pool = ["went up-tempo", "moved the pocket to slow the rush"]
            if rusher and _n(rusher.get("rush_yards")) >= 60:
                pool.append(f"leaned on the run game behind {rusher['name']}")
            if catcher and _n(catcher.get("rec_yards")) >= 60:
                pool.append(f"made {catcher['name']} the focal point")
            if qb and _n(qb.get("pass_yards")) >= 150:
                pool.append(f"put the game in {qb['name']}'s hands with more empty sets")
            out.append({"side": side, "unit": "offense", "text": (
                f"The {team['name']} offense {rng.choice(pool)} in the second half: {halves[side][0]} points before "
                f"halftime, {halves[side][1]} after")})
    if winner:
        loser = _other(winner)
        lead, at = flow["largest"][loser]
        if lead >= 10 and at is not None:
            out.append({"side": loser, "unit": "team", "text": (
                f"The {game[loser]['name']} blew a {lead}-point lead ({_score_text(events[at]['score_after'], game)} "
                f"in the {PERIOD_WORDS[events[at]['period']]})")})
    return out


def _standouts(game) -> dict:
    out = {}
    for side in ("away", "home"):
        team = game[side]
        if team["defense"] is not None:
            ranked = sorted(team["defense"], key=lambda d: -(2 * (d.get("sacks") or 0) + 2 * _n(d.get("ints"))
                                                              + 2 * _n(d.get("ff")) + 3 * _n(d.get("tds"))
                                                              + _n(d.get("tfl")) + _n(d.get("pd")) + _n(d.get("tackles")) / 5))
            lines = []
            for d in ranked[:2]:
                bits = [f"{_n(d.get('tackles'))} tackles"] if d.get("tackles") else []
                for key, label in (("sacks", "sack"), ("tfl", "TFL"), ("ints", "INT"), ("pd", "PD"), ("ff", "FF")):
                    v = d.get(key) or 0
                    if v:
                        bits.append(f"{v:g} {label}" + ("s" if key == "sacks" and v != 1 else ""))
                lines.append(f"{d['name']} ({d['pos']}): {', '.join(bits) or 'recorded line'}")
            out[team["abbr"]] = {"recorded": True, "players": lines}
        elif team.get("standouts"):
            out[team["abbr"]] = {"recorded": False, "players": [f"{n} ({p})" for n, p in team["standouts"][:2]]}
    return out


def _shape(events, flow, game, winner, overtime) -> tuple:
    margin = abs(game["away"]["score"] - game["home"]["score"])
    if not winner:
        return "tie", "Neither team could finish it"
    w, l = game[winner], game[_other(winner)]
    dec = events[flow["decisive"]] if flow["decisive"] is not None else None
    ht = flow["halftime"]
    w_ht, l_ht = ht[winner], ht[_other(winner)]
    if overtime and dec and dec["period"] == 4:
        return "overtime", f"The {w['name']} won it in overtime on {dec['play']}"
    if flow["winner_deficit"] >= 10:
        at = events[flow["deficit_at"]]
        return "comeback", (f"The {w['name']} came back from {flow['winner_deficit']} points down "
                            f"({_score_text(at['score_after'], game)} in the {PERIOD_WORDS[at['period']]})")
    if dec and dec["period"] == 3 and dec["clock"] <= 300:
        return "late_winner", (f"{dec['play']} with {_clock(dec['clock'])} left gave the {w['name']} "
                               f"the lead for good")
    if flow["lead_changes"] >= 3:
        return "back_and_forth", f"{flow['lead_changes']} lead changes before the {w['name']} pulled it out"
    if flow["largest"][winner][0] >= 21 and margin <= 14:
        return "garbage_time", (f"The {w['name']} led by {flow['largest'][winner][0]} before the {l['name']} "
                                f"scored late to make it look closer")
    if margin >= 21:
        return "rout", f"The {w['name']} ran away with it; it was {w_ht}-{l_ht} at halftime"
    if abs(w_ht - l_ht) <= 7:
        h2w = sum(e["points"] for e in events if e["side"] == winner and e["period"] >= 2)
        h2l = sum(e["points"] for e in events if e["side"] != winner and e["period"] >= 2)
        if h2w - h2l >= 10:
            return "pulled_away", f"Close at halftime, the {w['name']} won the second half {h2w}-{h2l}"
    if flow["winner_deficit"] == 0:
        return "wire_to_wire", f"The {w['name']} never trailed"
    return "took_control", (f"{dec['play']} in the {PERIOD_WORDS[dec['period']]} put the {w['name']} ahead "
                            f"for good" if dec else f"The {w['name']} took control")


# ------------------------------------------------------------------------------------------------ build ---
def build_script(game: dict) -> dict:
    """The game script for one game's inputs (fetch_inputs). Pure: same inputs → same script."""
    rng = random.Random(f"game-script:{game['game_id']}:{SCRIPT_VERSION}")
    margin = game["away"]["score"] - game["home"]["score"]
    winner = "away" if margin > 0 else "home" if margin < 0 else None
    units, tries, plans, all_plans = {}, {}, {}, {}
    for side in ("away", "home"):
        units[side], tries[side], plans[side], all_plans[side] = _build_units(side, game[side], game[_other(side)], rng)

    partitions = []
    quarters_known = all(game[s]["quarters"] for s in ("away", "home"))
    if quarters_known:
        per_side = {}
        for side in ("away", "home"):
            found = None
            # the cheapest way to make the score first, then the next ones, until one fits the captured quarters
            for j, plan in enumerate(all_plans[side][:12] or [plans[side]]):
                if j == 0:
                    u, t = units[side], tries[side]
                else:
                    u, t, _, _ = _build_units_from_plan(side, game, plan, random.Random(f"{game['game_id']}:{side}:{j}"))
                parts = _partitions(u, t, game[side]["quarters"], rng)
                if parts:
                    units[side], tries[side], plans[side], found = u, t, plan, parts
                    break
            per_side[side] = found
        if per_side["away"] and per_side["home"]:
            partitions = [{"away": rng.choice(per_side["away"]), "home": rng.choice(per_side["home"])} for _ in range(60)]
        else:
            quarters_known = False

    samples = []
    for _ in range(SAMPLES):
        events, overtime = _sample_events(units, tries, partitions, game, rng)
        flow = _flow(events, winner)
        samples.append((_drama(events, flow, winner, overtime), events, overtime, flow))
    samples.sort(key=lambda s: s[0])
    q = excitement_quantile(game.get("excitement_index"))
    idx = round(q * (len(samples) - 1))
    idx = min(len(samples) - 1, max(0, idx + rng.randint(-6, 6)))
    _, events, overtime, flow = samples[idx]
    events = [dict(e) for e in events]
    flow = _flow(events, winner)

    _describe(events, game, rng)
    moments = _moments(events, game, flow, winner, overtime, rng)
    _drives(events, game, rng)
    moments = _time_moments(moments, events, overtime, rng)
    shape, storyline = _shape(events, flow, game, winner, overtime)
    adjustments = _adjustments(events, game, flow, winner, rng)

    by_period = {s: [0] * 5 for s in ("away", "home")}
    for e in events:
        by_period[e["side"]][e["period"]] += e["points"]
    if quarters_known:
        by_period = {s: list(game[s]["quarters"]) for s in ("away", "home")}
    scoring = []
    for i, e in enumerate(events):
        tags = []
        before = events[i - 1]["score_after"] if i else {"away": 0, "home": 0}
        lead_before = before[e["side"]] - before[_other(e["side"])]
        lead_after = e["score_after"][e["side"]] - e["score_after"][_other(e["side"])]
        if i == 0:
            tags.append("opening score")
        elif lead_before <= 0 < lead_after:
            tags.append("go-ahead")
        if lead_after == 0:
            tags.append("tying")
        if flow["decisive"] == i:
            tags.append("lead for good")
        if e.get("set_up_by") == "takeaway":
            tags.append("set up by a takeaway")
        if e.get("fourth_down_conversion"):
            tags.append("drive kept alive on fourth down")
        scoring.append({"period": PERIOD_NAMES[e["period"]], "clock": _clock(e["clock"]), "team": game[e["side"]]["abbr"],
                        "play": e["play"], "drive": e.get("drive"), "score": _score_text(e["score_after"], game),
                        "tags": tags, "points": e["points"],
                        "score_after": {game[x]["abbr"]: e["score_after"][x] for x in ("away", "home")}})
    largest = {game[s]["abbr"]: flow["largest"][s][0] for s in ("away", "home")}
    return {
        "version": SCRIPT_VERSION,
        "game_id": game["game_id"],
        "matchup": f"{game['away']['name']} {game['away']['score']} @ {game['home']['name']} {game['home']['score']}"
                   + (" (OT)" if overtime else ""),
        "teams": {s: game[s]["abbr"] for s in ("away", "home")},
        "shape": shape,
        "storyline": storyline,
        "overtime": overtime,
        "quarter_scores": {game[s]["abbr"]: by_period[s][:5 if overtime else 4] for s in ("away", "home")},
        "quarter_scores_source": "captured box score" if quarters_known else "generated",
        "halftime": _score_text(flow["halftime"], game),
        "end_of_regulation": _score_text(flow["regulation"], game) if overtime else None,
        "lead_changes": flow["lead_changes"],
        "ties": flow["ties"],
        "largest_lead": largest,
        "winner_largest_deficit": flow["winner_deficit"] if winner else None,
        "scoring": scoring,
        "key_moments": [{"period": m["period_name"], "clock": _clock(m["clock"]), "team": game[m["side"]]["abbr"],
                         "type": m["type"], "text": m["text"]} for m in moments],
        "adjustments": [{"team": game[a["side"]]["abbr"], "unit": a["unit"], "text": a["text"]} for a in adjustments],
        "defensive_standouts": _standouts(game),
    }


def _build_units_from_plan(side, game, plan, rng):
    """_build_units with a given decomposition (for trying the next-cheapest one against captured quarters)."""
    team, opp = game[side], game[_other(side)]
    tds = _td_sources(team, opp, rng)
    if plan["dropped_td"]:
        rng.shuffle(tds)
        tds = tds[plan["dropped_td"]:]
    tds += [{"type": "defense_unknown", "scorer": None} for _ in range(plan["extra_td"])]
    units = [{"side": side, "kind": "td", "base": 6, **t} for t in tds]
    units += [{"side": side, "kind": "fg", "base": 3} for _ in range(plan["fgs"])]
    units += [{"side": side, "kind": "safety", "base": 2} for _ in range(plan["safeties"])]
    tries = [1] * plan["xp"] + [2] * plan["two"] + [0] * (plan["tds"] - plan["xp"] - plan["two"])
    return units, tries, plan, None


# ------------------------------------------------------------------------------------------------ render ---
def render_script(script: dict) -> str:
    """The script as prompt text for writers."""
    if not script:
        return ""
    t = script["teams"]
    qs = script["quarter_scores"]
    lines = [
        f"{script['matchup']}. Story: {script['storyline']}.",
        "By quarter: " + " | ".join(f"{abbr} {'-'.join(str(v) for v in qs[abbr])}" for abbr in (t["away"], t["home"]))
        + f" ({script['quarter_scores_source']}). Halftime: {script['halftime']}."
        + (f" End of regulation: {script['end_of_regulation']}." if script.get("end_of_regulation") else ""),
        f"Lead changes: {script['lead_changes']}; ties after a score: {script['ties']}; largest lead: "
        + ", ".join(f"{k} {v}" for k, v in script["largest_lead"].items()) + ".",
        "Scoring plays:",
    ]
    for s in script["scoring"]:
        drive = s.get("drive")
        dtext = (f" [{drive['plays']} play{'s' if drive['plays'] != 1 else ''}, {drive['yards']} yards, {drive['time']}]"
                 if drive else "")
        tags = f" ({', '.join(s['tags'])})" if s["tags"] else ""
        lines.append(f"- {s['period']} {s['clock']} {s['team']}: {s['play']}{dtext}. {s['score']}{tags}")
    if script["key_moments"]:
        lines.append("Key moments:")
        lines += [f"- {m['period']} {m['clock']} {m['team']}: {m['text']}" for m in script["key_moments"]]
    if script["adjustments"]:
        lines.append("Adjustments and turning points:")
        lines += [f"- {a['text']}" for a in script["adjustments"]]
    if script["defensive_standouts"]:
        lines.append("Defensive standouts:")
        for abbr, d in script["defensive_standouts"].items():
            note = "" if d["recorded"] else " (no defensive line recorded for this game: praise them in words, cite no numbers)"
            lines.append(f"- {abbr}: " + "; ".join(d["players"]) + note)
    return "\n".join(lines)


def script_summary(script: dict, moments: int = 3) -> dict:
    """A short version for briefs and social posts: storyline, halftime, the winning score, a few moments."""
    if not script:
        return {}
    decisive = next((s for s in script["scoring"] if "lead for good" in s["tags"]), None)
    out = {"storyline": script["storyline"], "halftime": script["halftime"]}
    if script.get("overtime"):
        out["overtime"] = True
    if decisive:
        out["winning_score"] = f"{decisive['period']} {decisive['clock']}: {decisive['play']} ({decisive['score']})"
    picked = sorted(script["key_moments"], key=lambda m: m["type"] not in ("takeaway", "sack", "missed_fg"))[:moments]
    if picked:
        out["key_moments"] = [f"{m['period']} {m['team']}: {m['text']}" for m in picked]
    return out


# ------------------------------------------------------------------------------------------------- data ---
def fetch_inputs(cur, game_id: int) -> dict | None:
    cur.execute("""
        SELECT g.id, g.season, g.week, COALESCE(g.game_type, 'REGULAR_SEASON'), g.excitement_index,
               g.away_team_id, at.name, at.abbreviation, g.away_score,
               g.home_team_id, ht.name, ht.abbreviation, g.home_score, g.status
        FROM games g JOIN teams at ON at.id = g.away_team_id JOIN teams ht ON ht.id = g.home_team_id
        WHERE g.id = %s
    """, (game_id,))
    r = cur.fetchone()
    if not r or r[13] != "COMPLETED" or r[8] is None or r[12] is None:
        return None
    game = {"game_id": r[0], "season": r[1], "week": r[2], "game_type": r[3],
            "excitement_index": round(float(r[4]), 1) if r[4] is not None else None,
            "away": {"id": r[5], "name": r[6], "abbr": r[7], "score": r[8]},
            "home": {"id": r[9], "name": r[10], "abbr": r[11], "score": r[12]}}

    cur.execute("""
        SELECT team_name, q1, q2, q3, q4, ot, two_point_conversions, fourth_down_conversions, fourth_down_attempts,
               turnovers, passing_yards, rushing_yards, estimated_fields, exact_fields
        FROM team_stats WHERE game_id = %s
    """, (game_id,))
    box = {row[0]: row for row in cur.fetchall()}

    cur.execute("""
        SELECT ps.team_id, COALESCE(p.full_name, ps.player_name), ps.position, ps.pass_yards, ps.pass_tds,
               ps.interceptions, ps.pass_long, ps.rush_yards, ps.rush_tds, ps.rush_long, ps.receptions, ps.rec_yards,
               ps.rec_tds, ps.rec_long, ps.fumb
        FROM player_stats ps LEFT JOIN players p ON p.id = ps.player_id
        WHERE ps.game_id = %s ORDER BY ps.team_id, ps.id
    """, (game_id,))
    offense = {}
    for row in cur.fetchall():
        offense.setdefault(row[0], []).append({
            "name": row[1], "pos": row[2], "pass_yards": row[3], "pass_tds": row[4], "ints": row[5],
            "pass_long": row[6], "rush_yards": row[7], "rush_tds": row[8], "rush_long": row[9], "receptions": row[10],
            "rec_yards": row[11], "rec_tds": row[12], "rec_long": row[13], "fumbles": row[14]})

    cur.execute("""
        SELECT team_id, COALESCE(full_name, player_name), def_tackles, def_sacks, def_tfl, def_ints, def_int_long,
               def_deflections, def_forced_fumbles, def_fumble_recoveries, def_tds, def_safeties,
               kick_fgm, kick_fga, kick_fg_long, kick_xpm, kick_xpa, kr_tds, kr_longest, pr_tds, pr_longest
        FROM game_stat_lines
        WHERE game_id = %s AND (def_tackles IS NOT NULL OR kick_fga IS NOT NULL OR kick_xpa IS NOT NULL
                                OR kr_returns IS NOT NULL OR pr_returns IS NOT NULL)
        ORDER BY team_id, player_id
    """, (game_id,))
    defense, kicking, returns = {}, {}, {}
    for row in cur.fetchall():
        tid, name = row[0], row[1]
        if row[2] is not None:
            defense.setdefault(tid, []).append({
                "name": name, "pos": None, "tackles": row[2], "sacks": float(row[3] or 0), "tfl": row[4],
                "ints": row[5], "int_long": row[6], "pd": row[7], "ff": row[8], "fr": row[9], "tds": row[10],
                "safeties": row[11]})
        if row[13] is not None or row[16] is not None:
            k = kicking.setdefault(tid, {"name": name, "fgm": 0, "fga": 0, "fg_long": 0, "xpm": 0, "xpa": 0})
            k["fgm"] += _n(row[12])
            k["fga"] += _n(row[13])
            k["xpm"] += _n(row[15])
            k["xpa"] += _n(row[16])
            k["fg_long"] = max(k["fg_long"], _n(row[14]))
        for kind, tds, longest in (("kick", row[17], row[18]), ("punt", row[19], row[20])):
            if _n(tds):
                returns.setdefault(tid, []).append({"name": name, "kind": kind, "tds": tds, "long": longest})
    if defense:
        cur.execute("""
            SELECT l.team_id, COALESCE(l.full_name, l.player_name), pse.position FROM game_stat_lines l
            JOIN player_seasons pse ON pse.player_id = l.player_id AND pse.season = l.season
            WHERE l.game_id = %s AND l.def_tackles IS NOT NULL
        """, (game_id,))
        pos = {(t, n): p for t, n, p in cur.fetchall()}
        for tid, rows in defense.items():
            for d in rows:
                d["pos"] = pos.get((tid, d["name"])) or "DEF"

    for side in ("away", "home"):
        team = game[side]
        tid = team["id"]
        b = box.get(team["name"])
        est, exact = (set(b[12] or []), set(b[13] or [])) if b else (set(), set())
        real = lambda f, v: v is not None and (f not in est or f in exact)
        quarters = None
        if b and all(v is not None for v in b[1:5]):
            qs = [int(v) for v in b[1:5]] + [int(b[5] or 0)]
            if sum(qs) == team["score"]:
                quarters = qs
        team["quarters"] = quarters
        team["two_pt_made"] = int(b[6]) if b and real("two_point_conversions", b[6]) else None
        team["fourth_down"] = ((int(b[7]), int(b[8])) if b and real("fourth_down_conversions", b[7])
                               and real("fourth_down_attempts", b[8]) else None)
        team["offense"] = offense.get(tid, [])
        team["defense"] = defense.get(tid)            # None = not captured (unknown, not zero)
        team["kicking"] = kicking.get(tid)
        if team["kicking"] and not team["kicking"]["fg_long"]:
            team["kicking"]["fg_long"] = None
        team["returns"] = returns.get(tid, [])
        ints = sum(_n(o["ints"]) for o in team["offense"])
        team["fumbles_lost"] = (max(0, int(b[9]) - ints) if b and quarters and b[9] is not None else None)
        team["total_yards"] = (int(b[10] or 0) + int(b[11] or 0)) if b and quarters else None
    game["overtime_known"] = any((game[s]["quarters"] or [0] * 5)[4] for s in ("away", "home")) or (
        game["away"]["score"] == game["home"]["score"] and all(game[s]["quarters"] for s in ("away", "home")))
    game["tie_possible_ot"] = game["away"]["score"] == game["home"]["score"]
    return game


def facts_hash(game: dict) -> str:
    """Hash of the recorded facts the script must honour (not of ratings, coaches or rosters, which drift)."""
    return hashlib.sha256(json.dumps(game, sort_keys=True, default=str).encode()).hexdigest()


def fetch_context(cur, game: dict) -> None:
    """Flavor that isn't a recorded fact of the game: kicker, top-rated defenders, head coach (set once, at build)."""
    for side in ("away", "home"):
        team = game[side]
        cur.execute("""
            SELECT COALESCE(p.full_name, p.player_name), pse.position FROM player_seasons pse
            JOIN players p ON p.id = pse.player_id
            WHERE pse.team_id = %s AND pse.season = %s AND pse.position = ANY(%s)
            ORDER BY pse.overall_rating DESC NULLS LAST LIMIT 3
        """, (team["id"], game["season"], list(DEF_POSITIONS)))
        team["standouts"] = cur.fetchall()
        cur.execute("""
            SELECT COALESCE(p.full_name, p.player_name) FROM player_seasons pse JOIN players p ON p.id = pse.player_id
            WHERE pse.team_id = %s AND pse.season = %s AND pse.position = 'K'
            ORDER BY pse.overall_rating DESC NULLS LAST LIMIT 1
        """, (team["id"], game["season"]))
        k = cur.fetchone()
        team["kicker"] = k[0] if k else None
        cur.execute("""
            SELECT c.coach_name, c.defensive_scheme, c.man_coverage FROM coach_tenures ct
            JOIN coaches c ON c.id = ct.coach_id
            WHERE ct.team_id = %s AND ct.start_season <= %s AND (ct.end_season IS NULL OR ct.end_season >= %s)
            ORDER BY ct.start_season DESC LIMIT 1
        """, (team["id"], game["season"], game["season"]))
        c = cur.fetchone()
        team["coach"] = {"name": c[0], "defensive_scheme": c[1], "man_coverage": c[2]} if c else None


def published_since(cur, game: dict, generated_at) -> bool:
    """True when an article or post went out after this game's script existed, so writers may have cited it."""
    cur.execute("SELECT 1 FROM news_feed WHERE season = %s AND week > %s AND created_at > %s LIMIT 1",
                (game["season"], game["week"], generated_at))
    return cur.fetchone() is not None


def ensure_script(cur, game_id: int, force: bool = False, unlock: bool = False) -> tuple:
    """(script, status) for one game, building and storing it when missing or when its facts changed.
    status: 'kept', 'built', 'rebuilt', 'locked' (force refused: the script has been published from, and
    unlock wasn't given) or 'not completed'. A change in the recorded facts always rebuilds: the story must
    follow a corrected box score."""
    game = fetch_inputs(cur, game_id)
    if not game:
        return None, "not completed"
    h = facts_hash(game)
    cur.execute("SELECT facts_hash, script, generated_at FROM game_scripts WHERE game_id = %s", (game_id,))
    row = cur.fetchone()
    if row and row[0] == h:
        if not force:
            return row[1], "kept"
        if not unlock and published_since(cur, game, row[2]):
            return row[1], "locked"
    fetch_context(cur, game)
    script = build_script(game)
    cur.execute("""
        INSERT INTO game_scripts (game_id, season, week, version, facts_hash, script, generated_at)
        VALUES (%s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (game_id) DO UPDATE SET season = EXCLUDED.season, week = EXCLUDED.week, version = EXCLUDED.version,
            facts_hash = EXCLUDED.facts_hash, script = EXCLUDED.script, generated_at = now()
    """, (game_id, game["season"], game["week"], SCRIPT_VERSION, h, json.dumps(script)))
    return script, "rebuilt" if row else "built"


def get_script(conn_info: str, game_id: int) -> dict | None:
    """The stored script for a game, built on the spot if the weekly run hasn't made it yet."""
    with psycopg.connect(conn_info) as conn:
        with conn.cursor() as cur:
            script, _ = ensure_script(cur, game_id)
        conn.commit()
    return script


def get_scripts(conn_info: str, game_ids) -> dict:
    out = {}
    with psycopg.connect(conn_info) as conn:
        with conn.cursor() as cur:
            for gid in game_ids:
                script, _ = ensure_script(cur, gid)
                if script:
                    out[gid] = script
        conn.commit()
    return out


def run(season: int | None = None, week: int | None = None, game_id: int | None = None, force: bool = False,
        conn_info: str = DATABASE_URL, verbose: bool = True, unlock: bool = False) -> dict:
    """Build every completed game's script for the season (or one week / one game) that is missing or stale."""
    counts = {"kept": 0, "built": 0, "rebuilt": 0, "locked": 0}
    with psycopg.connect(conn_info) as conn:
        with conn.cursor() as cur:
            if game_id is not None:
                ids = [game_id]
            else:
                if season is None:
                    cur.execute("SELECT current_season FROM league_state WHERE id = 1")
                    season = cur.fetchone()[0]
                cur.execute("SELECT id FROM games WHERE season = %s AND status = 'COMPLETED' AND (%s::int IS NULL OR week = %s) "
                            "ORDER BY week, id", (season, week, week))
                ids = [r[0] for r in cur.fetchall()]
            for gid in ids:
                _, status = ensure_script(cur, gid, force=force, unlock=unlock)
                if status in counts:
                    counts[status] += 1
                if status == "rebuilt" and verbose and not force:
                    print(f"   game {gid}: recorded facts changed, script rebuilt")
        conn.commit()
    if verbose:
        print(f"   game scripts: {counts['built']} built, {counts['rebuilt']} rebuilt, {counts['kept']} unchanged"
              + (f", {counts['locked']} locked (published from; --unlock-published to rebuild anyway)"
                 if counts["locked"] else ""))
    return counts


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--season", type=int)
    ap.add_argument("--week", type=int)
    ap.add_argument("--game", type=int)
    ap.add_argument("--force", action="store_true", help="rebuild even when the recorded facts haven't changed")
    ap.add_argument("--unlock-published", action="store_true",
                    help="with --force, also rebuild scripts that articles or posts published after them may cite")
    ap.add_argument("--print", action="store_true", help="print each script (with --game or --week)")
    a = ap.parse_args()
    run(a.season, a.week, a.game, force=a.force, unlock=a.unlock_published)
    if a.print:
        with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
            if a.game is not None:
                cur.execute("SELECT script FROM game_scripts WHERE game_id = %s", (a.game,))
            else:
                cur.execute("SELECT script FROM game_scripts WHERE week = %s AND season = COALESCE(%s, "
                            "(SELECT current_season FROM league_state WHERE id = 1)) ORDER BY game_id", (a.week, a.season))
            for (s,) in cur.fetchall():
                print("\n" + render_script(s))
