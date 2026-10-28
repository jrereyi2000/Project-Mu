"""
Fill-in players for 2026-2034 (docs/done/HISTORY_BACKFILL_PLAN.md phase 3, §3.6; PROJECT_SPEC §3.3k).

After player_lines, each team-season's leftover (its team total minus the cast's lines) is what players we have no
record of produced: post-2026 draftees who washed out or retired before 2035 (B7). A leftover the size of a starter
(STARTER x FILL_SHARE in the family's key stat) becomes a fill-in at that position, taking the family's leftover
for that season up to FILL_CAP starters' seasons. Consecutive seasons of one team and family are one fill-in (one career); smaller
leftovers stay unassigned, like untracked depth players. A record holder the cast doesn't know (Bayo Ojulari)
becomes a named fill-in: a rookie that season, on that team.

Names are drawn from first and last names of real players (nflverse), never a real pair: a drawn name that is a
real player's, or another fill-in's, is drawn again. Each fill-in gets players.origin = 'HISTORY_FILLIN' on apply.
build() is pure.
"""
import random
from collections import defaultdict

# family -> (position, key stat, a typical starter's season, the family's stats)
FAMILIES = {
    "pass": ("QB", "pass_yards", 3900, ("pass_attempts", "completions", "pass_yards", "pass_tds", "interceptions",
                                        "pass_sacks")),
    "rush": ("HB", "rush_yards", 1000, ("rush_attempts", "rush_yards", "rush_tds", "fumb", "rush_btk", "rush_yac")),
    "rec": ("WR", "rec_yards", 950, ("receptions", "rec_yards", "rec_tds", "rec_rac")),
    "sacks": ("REDG", "def_sacks", 9, ("def_sacks", "def_tfl")),
    "cover": ("CB", "def_deflections", 10, ("def_deflections", "def_ints", "def_int_yds")),
    "tackles": ("MIKE", "def_solo", 70, ("def_solo", "def_assists")),
    "kick": ("K", "kick_fgm", 24, ("kick_fgm", "kick_fga", "kick_xpm", "kick_xpa")),
    "punt": ("P", "punt_punts", 65, ("punt_punts", "punt_yards", "punt_net_yards", "punt_inside_20")),
}                                           # offensive linemen carry only sacks allowed: that leftover stays unassigned
FILL_SHARE = 0.6
FILL_CAP = 1.2            # a fill-in's season is at most this many starters' seasons (the rest stays unassigned)
DEBUT_AGE = {"QB": 23, "HB": 22, "WR": 22, "REDG": 23, "CB": 22, "MIKE": 23, "K": 24, "P": 24}


def build(leftover, real_names, taken_names=(), known=(), seed=1):
    """leftover: {(team, season): {stat: n}}; real_names: [(first, last)]; taken_names: full names in use;
    known: [{name, team, season, position}] fill-ins the record book names. Returns (fillins [{key, full_name,
    name, position, first, seasons {season: team}, lines {season: {stat: n}}}], unassigned {(team, season): {stat}})
    (pure)."""
    rng = random.Random(seed)
    firsts = sorted({f for f, _ in real_names if f})
    lasts = sorted({l for _, l in real_names if l})
    taken = {n.lower() for n in taken_names} | {f"{f} {l}".lower() for f, l in real_names}
    left = {k: dict(v) for k, v in leftover.items()}

    def new_name():
        for _ in range(1000):
            full = f"{rng.choice(firsts)} {rng.choice(lasts)}"
            if full.lower() not in taken:
                taken.add(full.lower())
                return full
        raise ValueError("no unused name")

    runs = defaultdict(list)                               # (team, family) -> seasons with a starter-sized gap
    for (team, y), row in sorted(left.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        for fam, (pos, stat, starter, _stats) in FAMILIES.items():
            if row.get(stat, 0) >= FILL_SHARE * starter:
                runs[(team, fam)].append(y)
    out = []
    for (team, fam), years in sorted(runs.items()):
        pos, stat, starter, stats = FAMILIES[fam]
        chunks, cur = [], [years[0]]
        for y in years[1:]:
            (cur.append(y) if y == cur[-1] + 1 else (chunks.append(cur), cur := [y]))
        chunks.append(cur)
        for chunk in chunks:
            full = new_name()
            first = chunk[0] - rng.choice((0, 0, 1, 2))   # sometimes a backup year or two before he started
            lines = {}
            for y in chunk:
                gap = left[(team, y)].get(stat, 0)
                frac = min(1.0, FILL_CAP * starter / gap) if gap else 1.0
                lines[y] = {s: int(round(left[(team, y)].get(s, 0) * frac)) for s in stats}
                for s in stats:
                    left[(team, y)][s] = left[(team, y)].get(s, 0) - lines[y][s]
            out.append(_member(full, pos, team, first, chunk, lines))
    for k in known:
        full = k["name"]
        taken.add(full.lower())
        out.append(_member(full, k["position"], k["team"], k["season"], [k["season"]], {k["season"]: {}}))
    unassigned = {k: {s: v for s, v in row.items() if v} for k, row in left.items()}
    return out, unassigned


def _member(full, pos, team, first, seasons, lines):
    parts = full.split()
    return {"key": f"f{abs(hash((full, team, first))) % 10 ** 9}", "kind": "fillin", "full_name": full,
            "name": f"{parts[0][0]}. {' '.join(parts[1:])}", "position": pos, "first": first, "team": team,
            "seasons": {y: team for y in seasons}, "lines": lines,
            "ages": {y: DEBUT_AGE.get(pos, 23) + (y - first) for y in seasons},
            "exp": {y: y - first for y in seasons}}


def real_names(path):
    import csv
    with open(path) as f:
        return [(r.get("first_name"), r.get("last_name")) for r in csv.DictReader(f)]
