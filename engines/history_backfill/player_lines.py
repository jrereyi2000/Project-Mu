# @AUDIT-STATUS: OBSOLETE_FRESH_SAVE (mu-history)
# @REPLACED-BY: NONE (Mu 2026-2036 history discarded; AUDIT_LEDGER.md §3b)
"""
2026-2034 player season lines (docs/done/HISTORY_BACKFILL_PLAN.md phase 3, §3.5; PROJECT_SPEC §3.3k).

Inputs: the cast (cast.py), team totals (team_totals.py), each pool player's remainder (remainder.py: career totals
minus 2035-2036 minus real pre-2026 minus canon rows), the record book and awards.

1. Shares: within a team-season every member is ranked in his group by quality (talent x age curve x experience
   ramp); each stat's team total is split by group (STAT_GROUPS) and, inside a group, by rank (RANK_SHARES).
   share x team total is each member's provisional line.
2. Pool players with a remainder: each stat's remainder is spread over his seasons in proportion to his provisional lines (exact
   integer sums, largest remainder first). Pinned seasons (a record he holds, a rookie record) take their stored
   value first; game records set a floor. Untracked pre-2026 columns (broken tackles, YAC, run after catch, sacks
   allowed for veterans) keep only his 2026-2034 share. Negative remainders count as zero (listed).
3. Free players (vets, and pool players who left after 2035, so have no career row): each team-season's total
   minus the constrained players' lines is split among them by their provisional
   shares, capped at VET_CAP x provisional; whatever is left is the fill-in step's leftover. A vet's pins apply too.
4. Records: no generated season that isn't a record-book entry reaches the NFL top 10 (below the current 10th
   value) or a franchise record as it stood that season (rookie categories: rookie seasons only); an excess moves to
   the pool player's other seasons, or is dropped for a vet.
5. Awards: MVP / OPOY seasons are top 3 at the position in the key stat and DPOY seasons top 3 in sacks, tackles or
   interceptions; the gap is taken from the pool player's other seasons or added to a vet's line. OROY / DROY
   seasons must be rookie seasons (listed when not).

generate() is pure apart from its inputs; problems are returned, never raised.
"""
import math
import random
import zlib
from collections import defaultdict

PASS = ("pass_attempts", "completions", "pass_yards", "pass_tds", "interceptions", "pass_sacks")
RUSH = ("rush_attempts", "rush_yards", "rush_tds", "fumb", "rush_btk", "rush_yac")
REC = ("receptions", "rec_yards", "rec_tds", "rec_rac")
DEF = ("def_solo", "def_assists", "def_tfl", "def_sacks", "def_ints", "def_int_yds", "def_deflections",
       "def_forced_fumbles", "def_fumble_recoveries", "def_tds", "def_safeties")
KICK = ("kick_fgm", "kick_fga", "kick_xpm", "kick_xpa")
PUNT = ("punt_punts", "punt_yards", "punt_net_yards", "punt_inside_20")
RET = ("kr_returns", "kr_yards", "pr_returns", "pr_yards")
BLK = ("blk_sacks_allowed",)
LINE_STATS = PASS + RUSH + REC + DEF + KICK + PUNT + RET + BLK
# stats the team totals don't carry: a ratio of a carried one (2035-2036 league ratios)
DERIVED = {"rush_btk": ("rush_attempts", 0.085), "rush_yac": ("rush_yards", 0.62), "rec_rac": ("rec_yards", 0.42)}

# each stat family's split by group
STAT_GROUPS = {
    "pass": {"QB": 1.0},
    "rush": {"RB": 0.84, "QB": 0.12, "WR": 0.03, "TE": 0.01},
    "rec": {"WR": 0.6, "TE": 0.24, "RB": 0.16},
    "tackles": {"LB": 0.4, "S": 0.25, "CB": 0.17, "DT": 0.09, "EDGE": 0.09},
    "sacks": {"EDGE": 0.48, "DT": 0.26, "LB": 0.18, "S": 0.04, "CB": 0.04},
    "tfl": {"EDGE": 0.3, "DT": 0.3, "LB": 0.3, "S": 0.05, "CB": 0.05},
    "ints": {"CB": 0.48, "S": 0.36, "LB": 0.14, "EDGE": 0.01, "DT": 0.01},
    "pd": {"CB": 0.55, "S": 0.28, "LB": 0.13, "EDGE": 0.02, "DT": 0.02},
    "ff": {"EDGE": 0.3, "LB": 0.25, "S": 0.15, "CB": 0.15, "DT": 0.15},
    "kick": {"K": 1.0}, "punt": {"P": 1.0},
    "ret": {"WR": 0.7, "RB": 0.15, "CB": 0.15},            # Madden: one primary returner takes most returns
    "blk": {"OL": 1.0},
}
FAMILY = {**{s: "pass" for s in PASS}, **{s: "rush" for s in RUSH}, **{s: "rec" for s in REC},
          "def_solo": "tackles", "def_assists": "tackles", "def_tfl": "tfl", "def_sacks": "sacks", "def_ints": "ints",
          "def_int_yds": "ints", "def_deflections": "pd", "def_forced_fumbles": "ff", "def_fumble_recoveries": "ff",
          "def_tds": "ints", "def_safeties": "sacks", **{s: "kick" for s in KICK}, **{s: "punt" for s in PUNT},
          **{s: "ret" for s in RET}, "blk_sacks_allowed": "blk"}
# share by rank inside a group (the rest of the group splits the last share evenly). Calibrated to 2035-2036 team
# concentration (JR 2026-10-26: the first shares spread production too thin): the starting QB ~96% of passing, the
# top back ~56% of rushing, the top three receivers ~72% of receiving.
RANK_SHARES = {
    ("pass", "QB"): (0.97, 0.025, 0.005), ("rush", "QB"): (0.97, 0.03),
    ("rush", "RB"): (0.66, 0.24, 0.07, 0.03), ("rec", "RB"): (0.62, 0.28, 0.1),
    ("rush", "WR"): (0.5, 0.3, 0.2), ("rush", "TE"): (1.0,),
    ("rec", "WR"): (0.41, 0.3, 0.16, 0.08, 0.03, 0.02), ("rec", "TE"): (0.82, 0.15, 0.03),
    ("kick", "K"): (1.0,), ("punt", "P"): (1.0,),
    ("blk", "OL"): (0.19, 0.19, 0.19, 0.19, 0.19, 0.03, 0.02),
    ("ret", "WR"): (0.05, 0.85, 0.1), ("ret", "RB"): (0.1, 0.8, 0.1), ("ret", "CB"): (0.1, 0.8, 0.1),
    # tackles and sacks concentrate on the starters (2035-2036: ~80 100-tackle and ~28 10-sack seasons a year)
    ("tackles", "LB"): (0.42, 0.33, 0.15, 0.06, 0.04), ("tackles", "S"): (0.45, 0.4, 0.1, 0.05),
    ("tackles", "CB"): (0.3, 0.28, 0.2, 0.12, 0.1), ("sacks", "EDGE"): (0.5, 0.3, 0.12, 0.08),
}
DEFAULT_RANKS = (0.3, 0.25, 0.18, 0.12, 0.08, 0.05, 0.02)
PEAK = {"QB": (27, 34), "RB": (23, 27), "WR": (24, 29), "TE": (25, 30), "OL": (25, 31), "EDGE": (25, 30),
        "DT": (25, 30), "LB": (24, 29), "CB": (24, 29), "S": (24, 30), "K": (25, 37), "P": (25, 37)}
DECLINE = 0.08            # per year past the peak
VET_CAP = 1.4             # a vet takes at most this many times his provisional line
SOFT_MARGIN = 1.05        # soft caps: 5% above the captured 2035-2036 season maximum

# record-book categories -> line stat (season categories pin the holder; game categories are floors)
RECORD_STAT = {"pass_yards": "pass_yards", "pass_tds": "pass_tds", "receptions": "receptions",
               "rec_yards": "rec_yards", "rec_tds": "rec_tds", "rush_yards": "rush_yards", "rush_tds": "rush_tds",
               "sacks": "def_sacks", "def_interceptions": "def_ints", "def_tds": "def_tds", "fgm": "kick_fgm",
               "tackles": "def_tackles", "scrimmage_tds": "scrimmage_tds"}   # scrimmage: rush + rec TDs (_scrimmage)
AWARD_KEY = {"QB": "pass_yards", "RB": "rush_yards", "WR": "rec_yards", "TE": "rec_yards", "EDGE": "def_sacks",
             "DT": "def_sacks", "LB": "def_tackles", "CB": "def_ints", "S": "def_ints"}


# ------------------------------------------------------------------------------------------------ quality ----

def curve(age, exp, group):
    """Age and experience multiplier (pure): a rookie ramp, a flat peak, a yearly decline after it."""
    lo, hi = PEAK.get(group, (25, 30))
    c = 1.0
    if age is not None:
        if age < lo:
            c = max(0.55, 1 - 0.06 * (lo - age))
        elif age > hi:
            c = max(0.15, 1 - DECLINE * (age - hi))
    if exp is not None and exp < 2:
        c *= (0.78, 0.9)[exp] if exp >= 0 else 0.78
    return c


def talent(m):
    if m["kind"] == "pool":
        ovr = m.get("overall") or 65
        t = min(1.2, max(0.05, (ovr - 55) / 35))
        t += {"Superstar X-Factor": 0.2, "Superstar": 0.12, "Star": 0.05}.get(m.get("dev") or "", 0)
        return t
    return 0.12 + 0.62 * min(1.3, m.get("production") or 0)


# --------------------------------------------------------------------------------------------- allocation ----

def split_exact(total, weights):
    """Integers summing to `total` in proportion to weights (largest remainder); all-zero weights split evenly
    (pure)."""
    total = int(round(total))
    keys = list(weights)
    if not keys:
        return {}
    s = sum(max(0.0, w) for w in weights.values())
    raw = {k: total * (max(0.0, weights[k]) / s if s > 0 else 1 / len(keys)) for k in keys}
    out = {k: int(math.floor(v)) for k, v in raw.items()}
    left = total - sum(out.values())
    for k in sorted(keys, key=lambda k: raw[k] - out[k], reverse=True)[:max(0, left)]:
        out[k] += 1
    return out


# allocation order for a constrained player: a stat capped by another comes after it. CAPPED_BY: a count that
# can't exceed its base (completions <= attempts, TDs <= catches); NEEDS: yards that need at least one attempt.
CAPPED_BY = {"completions": "pass_attempts", "kick_fgm": "kick_fga", "kick_xpm": "kick_xpa",
             "pass_tds": "completions", "interceptions": "pass_attempts", "rush_tds": "rush_attempts",
             "rec_tds": "receptions"}            # not rush_btk: Madden counts a receiver's broken tackles there too
NEEDS = {"pass_yards": "pass_attempts", "rush_yards": "rush_attempts", "rush_yac": "rush_attempts",
         "rec_yards": "receptions", "rec_rac": "receptions", "def_int_yds": "def_ints", "punt_yards": "punt_punts",
         "punt_net_yards": "punt_punts", "punt_inside_20": "punt_punts", "kr_yards": "kr_returns",
         "pr_yards": "pr_returns"}
BASES = ("pass_attempts", "rush_attempts", "receptions", "kick_fga", "kick_xpa")
ALLOC_ORDER = (tuple(s for s in LINE_STATS if s not in CAPPED_BY and s not in NEEDS)
               + ("completions",) + tuple(s for s in CAPPED_BY if s != "completions") + tuple(NEEDS))


def split_capped(total, weights, caps):
    """split_exact with per-key caps: an overflow moves to the keys still under their cap (pure)."""
    out = {k: 0 for k in weights}
    left, open_ = int(total), dict(weights)
    for _ in range(20):
        if left <= 0 or not open_:
            break
        part = split_exact(left, open_)
        left = 0
        for k, v in part.items():
            room = caps[k] - out[k] if k in caps else v
            take = min(v, max(0, room))
            out[k] += take
            left += v - take
        open_ = {k: w for k, w in open_.items() if k not in caps or out[k] < caps[k]}
        if open_ and not any(w > 0 for w in open_.values()):
            open_ = {k: 1.0 for k in open_}
    return out, left


def allocate(total, weights, pins, floors, caps):
    """One stat of one constrained player over his seasons: pins first, floors as pins when the split falls short
    of them, the rest split by weight under the caps. Returns ({season: n}, error or None) (pure)."""
    fixed = dict(pins)
    err = None
    for _ in range(len(weights) + 1):
        rest = total - sum(fixed.values())
        if rest < 0:
            err = f"pinned {sum(fixed.values())} above the remainder {total}"
            rest = 0
        free = {y: w for y, w in weights.items() if y not in fixed}
        got, unplaced = split_capped(rest, free, {y: c for y, c in caps.items() if y in free})
        if unplaced:                                     # the exact sum wins over the cap
            err = f"{unplaced} more than its seasons can hold under their caps"
            for y, v in split_exact(unplaced, free).items():
                got[y] += v
        short = {y: floors[y] for y in free if y in floors and got[y] < floors[y]}
        if not short:
            return {**fixed, **got}, err
        fixed.update(short)
    return {**fixed, **got}, err


def shares(members, seed=1, pins=None):
    """{(key, season): {family: share of the team total}} from ranks within each team-season and group (pure). A
    record holder (a pinned season) ranks first in his group that season: he was the starter."""
    rng = random.Random(seed)
    roster = defaultdict(list)
    for m in members:
        t = talent(m)
        for y, team in m["seasons"].items():
            q = t * curve(m["ages"].get(y), m["exp"].get(y), m["group"]) * rng.uniform(0.9, 1.1)
            if pins and (m["key"], y) in pins:
                q += 100
            roster[(team, y)].append((q, m))
    out = defaultdict(dict)
    for (team, y), ms in roster.items():
        by_group = defaultdict(list)
        for q, m in ms:
            by_group[m["group"]].append((q, m))
        for fam, groups in STAT_GROUPS.items():
            for g, gshare in groups.items():
                ranked = sorted(by_group.get(g, []), key=lambda x: -x[0])
                ranks = RANK_SHARES.get((fam, g), DEFAULT_RANKS)
                n = len(ranked)
                for i, (q, m) in enumerate(ranked):
                    s = ranks[i] if i < len(ranks) else 0.0
                    if n < len(ranks) and i == n - 1:
                        s += sum(ranks[n:])                            # a thin group's last man takes the rest
                    out[(m["key"], y)][fam] = gshare * s
    return out


def provisional(members, sh, totals):
    """{(key, season): {stat: float}}: share x the team total (DERIVED stats from their base) (pure)."""
    out = {}
    for m in members:
        for y, team in m["seasons"].items():
            tot = totals.get((team, y), {})
            row = {}
            for stat in LINE_STATS:
                if stat in DERIVED:
                    base, r = DERIVED[stat]
                    row[stat] = sh[(m["key"], y)].get(FAMILY[base], 0) * tot.get(base, 0) * r
                else:
                    row[stat] = sh[(m["key"], y)].get(FAMILY[stat], 0) * tot.get(stat, 0)
            out[(m["key"], y)] = row
    return out


def generate(cast, totals, remainders, canon, pins, floors, ceilings, awards, seed=1, soft=None):
    """cast: cast.build() members; totals: team_totals.draw(); remainders {player_id: {stat: value}};
    canon {(player_id, season): {stat: value}} (kept, not generated); pins {(key, season): {stat: value}};
    floors {(key, season): {stat: min}}; ceilings: f(member, season, stat) -> max or None; awards
    [(season, award, key)]. Returns (lines {(key, season): {stat: int}}, leftover {(team, season): {stat}},
    problems [str])."""
    members = cast
    by_key = {m["key"]: m for m in members}
    # constrained: a pool player with a remainder; everyone else (vets, and pool players who left after 2035 and so
    # have no career row) fills the team totals freely
    fixed_keys = {m["key"] for m in members if m["kind"] == "pool" and m["player_id"] in remainders}
    for m in members:
        m["constrained"] = m["key"] in fixed_keys
    sh = shares(members, seed, pins)
    prov = provisional(members, sh, totals)
    lines, problems, adjustments = {}, [], []

    # 2. pool players: remainders spread over their seasons
    for m in members:
        if not m["constrained"]:
            continue
        rem = remainders.get(m["player_id"])
        for y in m["seasons"]:
            lines[(m["key"], y)] = dict(canon.get((m["player_id"], y), {}))
        for stat in ALLOC_ORDER:
            r = max(0, int(round(rem.get(stat) or 0)))
            if (rem.get(stat) or 0) < -0.5:
                problems.append(f"{m['full_name']}: {stat} remainder {rem[stat]:g} counted as 0")
            # a canon row fixes its own columns only; his other stats still spread over that season
            seasons = [y for y in m["seasons"] if stat not in canon.get((m["player_id"], y), {})]
            if not seasons:
                closed = [y for y in m["seasons"] if (m["player_id"], y) in canon]
                if r and closed:                         # Madden's career beats a seeded number: the seeded row grows
                    y = closed[-1]
                    lines[(m["key"], y)][stat] = lines[(m["key"], y)].get(stat, 0) + r
                    adjustments.append((m["player_id"], y, stat, canon[(m["player_id"], y)][stat],
                                        lines[(m["key"], y)][stat]))
                continue
            caps = {}
            if stat in CAPPED_BY:
                caps = {y: lines[(m["key"], y)].get(CAPPED_BY[stat], 0) for y in seasons}
            elif stat in NEEDS:
                caps = {y: (0 if not lines[(m["key"], y)].get(NEEDS[stat], 0) else 10 ** 7) for y in seasons}
            got, err = allocate(r, {y: prov[(m["key"], y)][stat] for y in seasons},
                                {y: pins[(m["key"], y)][stat] for y in seasons if stat in pins.get((m["key"], y), {})},
                                {y: floors[(m["key"], y)][stat] for y in seasons if stat in floors.get((m["key"], y), {})},
                                caps)
            if err:
                problems.append(f"{m['full_name']}: {stat} {err}")
            for y, v in got.items():
                lines[(m["key"], y)][stat] = v

    # 3. vets fill the team totals
    team_members = defaultdict(list)
    for m in members:
        for y, team in m["seasons"].items():
            team_members[(team, y)].append(m)
    leftover = {}
    for (team, y), ms in team_members.items():
        tot = totals.get((team, y), {})
        vets = [m for m in ms if not m["constrained"]]
        for m in vets:
            lines[(m["key"], y)] = {}
        # free players fill each stat group as a block: one factor from the group's key stat (what's left of the
        # team total over what their shares ask for, at most VET_CAP) scales every stat of the group, so their
        # ratios stay the team's (punting average, yards per catch, interceptions to return yards)
        room_of = {}
        for stat in LINE_STATS:
            target = tot.get(stat) if stat not in DERIVED else tot.get(DERIVED[stat][0], 0) * DERIVED[stat][1]
            used = sum(lines.get((m["key"], y), {}).get(stat, 0) for m in ms if m["constrained"])
            room_of[stat] = max(0.0, (target or 0) - used)
        given = defaultdict(float)
        for group, (key_stat, stats) in FREE_GROUPS.items():
            need = sum(prov[(m["key"], y)][key_stat] for m in vets)
            f = min(VET_CAP, room_of[key_stat] / need) if need > 0 else 0.0
            for stat in stats:                       # the group total split exactly: small counts don't round away
                weights = {m["key"]: prov[(m["key"], y)][stat] for m in vets}
                for k, v in split_exact(sum(weights.values()) * f, weights).items():
                    if v:
                        lines[(k, y)][stat] = v
                        given[stat] += v
        for m in vets:
            pinned = pins.get((m["key"], y), {})
            for stat, v in pinned.items():
                given[stat] += v - lines[(m["key"], y)].get(stat, 0)
                lines[(m["key"], y)][stat] = v
            # a record season carries the rest of its line at league starter rates (attempts, TDs, INTs behind
            # the yards), so a free record holder reads like the starter he was
            for other, v in pin_line(pinned).items():
                given[other] += v - lines[(m["key"], y)].get(other, 0)
                lines[(m["key"], y)][other] = v
        leftover[(team, y)] = {stat: max(0, int(round(room_of[stat] - given[stat]))) for stat in LINE_STATS}

    _floors(lines, {k: v for k, v in floors.items() if not by_key[k[0]]["constrained"]})
    problems += _ceilings(lines, members, ceilings, pins)
    problems += _awards(lines, members, by_key, awards, pins)
    problems += _ceilings(lines, members, ceilings, pins)        # records outrank awards: an award boost stays capped
    problems += _soft_caps(lines, members, soft or {}, pins)
    for key in lines:
        row = lines[key]
        if "def_solo" in row or "def_assists" in row:
            row["def_tackles"] = row.get("def_solo", 0) + row.get("def_assists", 0)
        if not by_key[key[0]]["constrained"]:
            support_pins(row, pins.get(key, {}), lambda base, k=key: ceilings(by_key[k[0]], k[1], base))
            consistent(row)                              # a constrained line keeps Madden's own sums
    gp = games_played(lines, members, sh, seed)
    for key, g in gp.items():
        lines[key]["games_played"] = g
    for key in [k for k, row in lines.items() if not any(v for s, v in row.items() if s != "games_played")]:
        del lines[key]                                   # a season with no stats isn't a line (practice squad)
    return lines, leftover, problems + [f"canon adjustment: player {pid} {y} {stat} {old} -> {new}"
                                        for pid, y, stat, old, new in adjustments]


def consistent(row):
    """Stats that can't exceed another: completions <= attempts, FG / XP made <= tried (raise the tries); yards
    after contact, run after catch and net punting <= the yards they're part of (lower the part) (pure, in place)."""
    for made, tried in (("completions", "pass_attempts"), ("kick_fgm", "kick_fga"), ("kick_xpm", "kick_xpa")):
        if row.get(made, 0) > row.get(tried, 0):
            row[tried] = row[made]
    for part, whole in (("rush_yac", "rush_yards"), ("rec_rac", "rec_yards"), ("punt_net_yards", "punt_yards")):
        if row.get(part, 0) > max(0, row.get(whole, 0)):
            row[part] = max(0, row.get(whole, 0))
    for stat, base in CAPPED_BY.items():                 # a count can't exceed its base...
        if stat not in ("completions", "kick_fgm", "kick_xpm") and row.get(stat, 0) > row.get(base, 0):
            row[stat] = row.get(base, 0)
    for stat, base in NEEDS.items():                     # ...and yards need an attempt
        if row.get(stat) and not row.get(base):
            row[stat] = 0
    sacks = int(math.floor(row.get("def_sacks", 0) or 0))   # a sack is a tackle, and a tackle for loss
    if sacks:
        row["def_tfl"] = max(row.get("def_tfl", 0), sacks)
        if row.get("def_solo", 0) + row.get("def_assists", 0) < sacks:
            row["def_solo"] = sacks - row.get("def_assists", 0)


# free players' stat groups: (key stat that sets the group's factor, the stats it scales)
FREE_GROUPS = {
    "pass": ("pass_attempts", PASS), "rush": ("rush_attempts", RUSH), "rec": ("receptions", REC),
    "tackles": ("def_solo", ("def_solo", "def_assists")), "sacks": ("def_sacks", ("def_sacks", "def_safeties")),
    "tfl": ("def_tfl", ("def_tfl",)), "ints": ("def_ints", ("def_ints", "def_int_yds", "def_tds")),
    "pd": ("def_deflections", ("def_deflections",)),
    "ff": ("def_forced_fumbles", ("def_forced_fumbles", "def_fumble_recoveries")),
    "kick": ("kick_fga", KICK), "punt": ("punt_punts", PUNT), "kr": ("kr_returns", ("kr_returns", "kr_yards")),
    "pr": ("pr_returns", ("pr_returns", "pr_yards")), "blk": ("blk_sacks_allowed", BLK)}

# a pinned record season needs its base: league ratios (2035-2036) for the attempts behind pinned yards and TDs
SUPPORT = {"pass_yards": (("pass_attempts", 1 / 7.3), ("completions", 0.65 / 7.3)),
           "pass_tds": (("completions", 1.0),), "rush_yards": (("rush_attempts", 1 / 4.6),),
           "rush_tds": (("rush_attempts", 1.0),), "rec_yards": (("receptions", 1 / 12.5),),
           "rec_tds": (("receptions", 1.0),)}


# 2035-2036 starters' rates (QBs with 300+ attempts, backs with 150+ carries, receivers with 50+ catches)
PIN_RATES = {"pass_yards": {"pass_attempts": 1 / 6.95, "completions": 0.612 / 6.95, "pass_tds": 0.047 / 6.95,
                            "interceptions": 0.010 / 6.95},
             "rec_yards": {"receptions": 1 / 12.6, "rec_tds": 0.084 / 12.6},
             "rush_yards": {"rush_attempts": 1 / 4.95, "rush_tds": 0.041 / 4.95}}
# yards behind a pinned TD or catch count in a record-level season (a 45-TD passer ~5,000 yards, an 18-TD receiver
# ~1,550, an 18-TD back ~1,800)
PIN_YARDS = {"pass_tds": ("pass_yards", 112), "rec_tds": ("rec_yards", 85), "receptions": ("rec_yards", 12.6),
             "rush_tds": ("rush_yards", 100)}


def pin_line(pinned):
    """The rest of a free player's record season from its pinned stats at league starter rates: yards from a pinned
    TD or catch count when the yards aren't pinned, then attempts, completions, TDs, INTs and catches from the yards
    (pinned values always win) (pure)."""
    out = {}
    for stat, (yards, per) in PIN_YARDS.items():
        if stat in pinned and yards not in pinned:
            out[yards] = int(round(pinned[stat] * per))
    for yards, rates in PIN_RATES.items():
        y = pinned.get(yards, out.get(yards))
        if y:
            for other, r in rates.items():
                if other not in pinned:
                    out[other] = int(round(y * r))
    return {k: v for k, v in out.items() if k not in pinned}


def support_pins(row, pinned, cap=lambda base: None):
    """Raise the attempts or catches behind a pinned stat toward what the record needs, never past the base's own
    record ceiling (cap(base) -> max or None) and never below one (pure, in place)."""
    for stat in pinned:
        for base, ratio in SUPPORT.get(stat, ()):
            need = max(1, int(math.ceil(row.get(stat, 0) * ratio)))
            ceiling = cap(base)
            if ceiling is not None and base not in pinned:
                need = max(1, min(need, ceiling))
            row[base] = max(row.get(base, 0), need)


def games_played(lines, members, sh, seed=1):
    """{(key, season): games} for every line with a stat: a starter (the top share of his group) plays 14-17, a
    backup fewer, by his share relative to the group's top (pure)."""
    rng = random.Random(seed + 7)
    top = defaultdict(float)
    for m in members:
        for y, team in m["seasons"].items():
            s = max(sh.get((m["key"], y), {}).values(), default=0)
            top[(team, y, m["group"])] = max(top[(team, y, m["group"])], s)
    out = {}
    for m in members:
        for y, team in m["seasons"].items():
            row = lines.get((m["key"], y))
            if not row or not any(v for v in row.values()):
                continue
            s = max(sh.get((m["key"], y), {}).values(), default=0)
            rel = s / top[(team, y, m["group"])] if top[(team, y, m["group"])] else 0.5
            out[(m["key"], y)] = max(1, min(17, int(round(17 * (0.4 + 0.6 * rel) - abs(rng.gauss(0, 1.5))))))
    return out


def _floors(lines, floors):
    for key, fl in floors.items():
        if key in lines:
            for stat, v in fl.items():
                if lines[key].get(stat, 0) < v:
                    lines[key][stat] = v


def _ceilings(lines, members, ceilings, pins):
    """Cap every non-pinned season at its ceiling; a pool player's excess moves to his other seasons that have room."""
    problems = []
    for m in members:
        for stat in LINE_STATS + ("def_tackles",):
            for _ in range(3):
                over = 0
                for y in m["seasons"]:
                    key = (m["key"], y)
                    if stat in pins.get(key, {}):
                        continue
                    cap = ceilings(m, y, stat)
                    v = lines.get(key, {}).get(stat, 0)
                    if cap is not None and v > cap:
                        lines[key][stat] = below_cap(cap, key, stat)
                        over += v - lines[key][stat]
                if not over or not m["constrained"]:
                    break
                room = {}
                for y in m["seasons"]:
                    cap = ceilings(m, y, stat)
                    if stat in pins.get((m["key"], y), {}):
                        continue
                    v = lines[(m["key"], y)].get(stat, 0)
                    room[y] = (cap - v) if cap is not None else max(1, v) * 10
                if sum(max(0, r) for r in room.values()) < over:
                    problems.append(f"{m['full_name']}: {over} {stat} above record ceilings with nowhere to go")
                    over = sum(max(0, r) for r in room.values())
                for y, v in split_exact(over, {y: max(0, r) for y, r in room.items()}).items():
                    lines[(m["key"], y)][stat] = lines[(m["key"], y)].get(stat, 0) + v
        problems += _scrimmage(lines, m, ceilings, pins)
    return problems


def below_cap(cap, key, stat):
    """A capped season lands a little under its cap, not on it: up to 4% (at least 1) below, fixed per player-season
    and stat (pure). Without it every capped leader read exactly the cap (127 catches every season)."""
    spread = max(1, int(cap * 0.04))
    return max(0, cap - (zlib.crc32(f"{key}|{stat}".encode()) % (spread + 1)))


def _soft_caps(lines, members, soft, pins):
    """Keep a season within the captured era's range (soft: {stat: max}): an excess moves to the player's other
    seasons with room; what has nowhere to go goes back where it came from (a constrained player's career sum wins;
    listed as a note) (pure apart from `lines`)."""
    notes = []
    for m in members:
        for stat, cap in soft.items():
            taken, room = {}, {}
            for y in m["seasons"]:
                key = (m["key"], y)
                v = lines.get(key, {}).get(stat, 0)
                if stat in pins.get(key, {}):
                    continue
                if v > cap:
                    keep = below_cap(int(cap), key, stat)
                    taken[y] = v - keep
                    lines[key][stat] = keep
                else:
                    room[y] = int(cap - v)
            over = sum(taken.values())
            if not over or not m["constrained"]:
                continue                                 # a free line just stays inside the range
            place = min(over, sum(room.values()))
            for y, v in split_exact(place, room).items():
                lines[(m["key"], y)][stat] = lines[(m["key"], y)].get(stat, 0) + v
            if place < over:
                for y, v in split_exact(over - place, taken).items():
                    lines[(m["key"], y)][stat] += v
                notes.append(f"note: {m['full_name']}: {over - place} {stat} above the 2035-2036 range with nowhere to go")
    return notes


def _scrimmage(lines, m, ceilings, pins):
    """Rushing + receiving TDs under the scrimmage-TD ceiling: the larger part gives up the excess; a constrained
    player's excess moves to his seasons with scrimmage room (pure apart from `lines`)."""
    problems = []
    td = lambda row: row.get("rush_tds", 0) + row.get("rec_tds", 0)
    moved = {"rush_tds": 0, "rec_tds": 0}
    for y in m["seasons"]:
        key = (m["key"], y)
        row = lines.get(key, {})
        cap = ceilings(m, y, "scrimmage_tds")
        if cap is None or td(row) <= cap:
            continue
        over = td(row) - cap
        free_parts = [p for p in ("rush_tds", "rec_tds") if p not in pins.get(key, {})]   # a record TD count stays
        for part in sorted(free_parts, key=lambda s: -row.get(s, 0)):
            take = min(over, row.get(part, 0))
            row[part] = row.get(part, 0) - take
            moved[part] += take
            over -= take
    if not m["constrained"]:
        return problems
    for part, n in moved.items():
        if not n:
            continue
        room = {}
        for y in m["seasons"]:
            row = lines.get((m["key"], y), {})
            cap = ceilings(m, y, "scrimmage_tds")
            base = "rush_attempts" if part == "rush_tds" else "receptions"
            if row.get(base) and part not in pins.get((m["key"], y), {}):
                room[y] = max(0, (cap - td(row)) if cap is not None else 10 ** 6)
        place = min(n, sum(room.values()))
        for y, v in split_exact(place, room).items():
            lines[(m["key"], y)][part] = lines[(m["key"], y)].get(part, 0) + v
        if place < n:
            problems.append(f"{m['full_name']}: {n - place} {part} above the scrimmage-TD ceilings with nowhere to go")
    return problems


def _awards(lines, members, by_key, awards, pins):
    problems = []
    season_rows = defaultdict(list)
    for m in members:
        for y in m["seasons"]:
            season_rows[(y, m["group"])].append(m)
    for y, award, key in awards:
        m = by_key.get(key)
        if m is None or y not in m["seasons"]:
            problems.append(f"{y} {award}: {key} has no {y} season")
            continue
        if award in ("OROY", "DROY"):
            if m["exp"].get(y) != 0:
                problems.append(f"{y} {award}: {m['full_name']} isn't a rookie in {y} (experience {m['exp'].get(y)})")
            continue
        if award not in ("MVP", "OPOY", "DPOY"):
            continue
        stat = AWARD_KEY.get(m["group"])
        if stat is None or stat in pins.get((key, y), {}):
            continue
        val = lambda mm: lines.get((mm["key"], y), {}).get(stat, 0) if stat != "def_tackles" else \
            lines.get((mm["key"], y), {}).get("def_solo", 0) + lines.get((mm["key"], y), {}).get("def_assists", 0)
        rivals = sorted((val(mm) for mm in season_rows[(y, m["group"])] if mm is not m), reverse=True)
        third = rivals[2] if len(rivals) > 2 else 0
        mine = val(m)
        if mine > third:
            continue
        need = third + 1 - mine
        part = "def_solo" if stat == "def_tackles" else stat
        if m["constrained"]:
            others = {yy: lines[(key, yy)].get(part, 0) for yy in m["seasons"] if yy != y
                      and part not in pins.get((key, yy), {})}
            take = min(need, sum(others.values()))
            for yy, v in split_exact(take, others).items():
                lines[(key, yy)][part] = lines[(key, yy)].get(part, 0) - v
            lines[(key, y)][part] = lines[(key, y)].get(part, 0) + take
            if take < need:
                problems.append(f"{y} {award}: {m['full_name']} short of the top 3 in {stat} by {need - take}")
        else:
            lines[(key, y)][part] = lines[(key, y)].get(part, 0) + need
    return problems


# --------------------------------------------------------------------------------------------------- inputs ----

def inputs(cur, cast_out, season=2036):
    """remainders, canon, pins, floors, ceilings and awards from the database, keyed for generate()."""
    from engines.history_backfill import cast as cast_mod
    from engines.history_backfill import remainder
    members = cast_out["members"]
    by_name = defaultdict(list)
    for m in members:
        by_name[cast_mod.name_key(m["full_name"] or m["name"])].append(m)
    by_pid = {m["player_id"]: m for m in members if m["player_id"]}

    def find(name, pid=None, kind=""):
        if pid and pid in by_pid:
            return by_pid[pid]
        groups = cast_mod.record_groups(kind)
        c = [m for m in by_name.get(cast_mod.name_key(name), []) if groups is None or m["group"] in groups]
        return c[0] if len(c) == 1 else None

    result, _, _ = remainder.build(cur, season)
    remainders = {}
    for pid, r in result.items():
        gen = dict(r["generate"])
        first = r.get("first_season")
        if r["untracked"] and first is not None and first < cast_mod.FIRST:
            open_ = r["open_seasons"]
            frac = open_ / (open_ + cast_mod.FIRST - first)
            for c in r["untracked"]:
                gen[c] = gen[c] * frac
        remainders[pid] = gen

    cols = [c for c in PASS + RUSH + REC if c not in ("rush_btk", "rush_yac", "rec_rac")]
    cur.execute(f"SELECT player_id, season, {', '.join(cols)} FROM season_stats WHERE source <> 'SYNTH' AND season BETWEEN %s AND %s "
                f"AND player_id IS NOT NULL", (cast_mod.FIRST, cast_mod.LAST))
    # a seeded canon row's zeros are "not stated", not facts: only its non-zero columns are kept
    canon = {(r[0], r[1]): {c: v for c, v in zip(cols, r[2:]) if v} for r in cur.fetchall()}

    pins, floors, unresolved = defaultdict(dict), defaultdict(dict), []
    entries = []
    for table in ("league_record_entries", "franchise_records"):
        cur.execute(f"SELECT category, holder, player_id, season, value FROM {table} WHERE source = 'BOOK' "
                    f"AND season BETWEEN %s AND %s", (cast_mod.FIRST, cast_mod.LAST))
        entries += cur.fetchall()
    for cat, holder, pid, y, value in entries:
        m = find(holder, pid, f"record:{cat}")
        base = cat.replace("rookie_", "").replace("season_", "", 1) if cat.startswith(("season_", "rookie_season_")) \
            else cat.replace("rookie_", "").replace("game_", "", 1) if cat.startswith(("game_", "rookie_game_")) else None
        stat = RECORD_STAT.get(base)
        if m is None or stat is None:
            unresolved.append((cat, holder, y))
            continue
        key, v = (m["key"], y), int(round(float(value)))
        if stat == "scrimmage_tds":
            continue          # rush + rec TDs: no line column to pin; the ceilings and _scrimmage handle it
        if cat.startswith(("season_", "rookie_season_")):
            if stat == "def_tackles":
                pins[key]["def_solo"] = int(round(v * 0.65))
                pins[key]["def_assists"] = v - pins[key]["def_solo"]
            else:
                pins[key][stat] = max(v, pins[key].get(stat, 0))
        elif stat != "def_tackles":
            floors[key][stat] = max(v, floors[key].get(stat, 0))

    # ceilings: below the NFL top 10 as it stands now, and below the franchise record as it stood that season
    cur.execute("SELECT category, value FROM league_record_entries")
    by_cat = defaultdict(list)
    for cat, v in cur.fetchall():
        by_cat[cat].append(float(v))
    tenth = {cat: sorted(vs, reverse=True)[9] for cat, vs in by_cat.items() if len(vs) >= 10}
    cur.execute("SELECT team_id, category, value, season, source FROM franchise_records")
    fr = defaultdict(list)
    for t, cat, v, y, src in cur.fetchall():
        fr[(t, cat)].append((y if src != "NFL" else 0, float(v)))
    stat_cats = defaultdict(list)
    for base, stat in RECORD_STAT.items():
        stat_cats[stat].append(base)

    def ceilings(m, y, stat):
        caps = []
        rookie = m["exp"].get(y) == 0
        for base in stat_cats.get(stat, ()):
            for cat in [f"season_{base}"] + ([f"rookie_season_{base}"] if rookie else []):
                if cat in tenth:
                    caps.append(tenth[cat] - 1)
                rows = fr.get((m["seasons"][y], cat))
                if rows:
                    before = [v for yy, v in rows if yy < y]
                    if before:
                        caps.append(max(before) - 1)
        return int(math.floor(min(caps))) if caps else None

    cur.execute("SELECT season, award_type, player_name, player_id FROM awards WHERE season BETWEEN %s AND %s "
                "AND award_type IN ('MVP', 'OPOY', 'DPOY', 'OROY', 'DROY')", (cast_mod.FIRST, cast_mod.LAST))
    awards = []
    for y, a, name, pid in cur.fetchall():
        m = find(name, pid)
        if m is None:
            unresolved.append((a, name, y))
        else:
            awards.append((y, a, m["key"]))
    # soft caps: a season stays within 5% of the captured 2035-2036 maximum (season_stat_lines; defense is the
    # listed leaders, so its maxima are real)
    view_cols = [c for c in LINE_STATS if c not in ("def_solo", "def_assists")] + ["def_solo", "def_assists"]
    cur.execute(f"SELECT {', '.join(f'max({c})' for c in view_cols)} FROM season_stat_lines "
                f"WHERE source = 'CAPTURE' AND season >= 2035")
    soft = {c: float(v) * SOFT_MARGIN for c, v in zip(view_cols, cur.fetchone()) if v}
    return {"remainders": remainders, "canon": canon, "pins": dict(pins), "floors": dict(floors),
            "ceilings": ceilings, "awards": awards, "unresolved": unresolved, "soft": soft}
