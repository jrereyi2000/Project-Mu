# @AUDIT-STATUS: OBSOLETE_FRESH_SAVE (mu-history)
# @REPLACED-BY: NONE (Mu 2026-2036 history discarded; AUDIT_LEDGER.md §3b)
"""
Who played where, 2026-2034 (docs/done/HISTORY_BACKFILL_PLAN.md phase 3, §3.5; PROJECT_SPEC §3.3k).

The cast is every player who gets 2026-2034 season lines, with a team for each season he played:
- pool: everyone on a 2035 or 2036 roster. His seasons run from his first season (season - years_of_experience) to
  2034. His team is his 2035 team (B16), his real 2026 team in 2026 when he was on a 2026 NFL roster, and the team an
  award or a record-book entry names for that season (a fact; the switch to the next known team comes halfway
  between them).
- vet: a real 2026 NFL player (data/history/rosters_2026.csv) who isn't in the pool: he retired during 2026-2034.
  His last season is drawn from his age and position (CAREER_END), later for a productive 2025 and never before a
  season an award or record names him in; his team is his 2026 team, with the same fact rule.
- a record holder who is neither (one name, Bayo Ojulari) is listed in `unknown` for the fill-in step.

build() is pure; load() reads the database and data/history/.
"""
import csv
import math
import os
import random
import re
from collections import defaultdict

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
HISTORY = os.path.join(ROOT, "data", "history")
FIRST, LAST = 2026, 2034
SEASONS = list(range(FIRST, LAST + 1))

# Madden position -> line group (what kind of stats he gets)
GROUP = {"QB": "QB", "HB": "RB", "FB": "RB", "WR": "WR", "TE": "TE",
         "LT": "OL", "LG": "OL", "C": "OL", "RG": "OL", "RT": "OL",
         "LEDG": "EDGE", "REDG": "EDGE", "DT": "DT", "SAM": "LB", "MIKE": "LB", "WILL": "LB",
         "CB": "CB", "FS": "S", "SS": "S", "K": "K", "P": "P"}
# ESPN 2026 roster position -> Madden positions to rotate through
ESPN_POS = {"QB": ["QB"], "RB": ["HB"], "FB": ["FB"], "WR": ["WR"], "TE": ["TE"], "OT": ["LT", "RT"],
            "G": ["LG", "RG"], "C": ["C"], "OL": ["LG"], "DE": ["LEDG", "REDG"], "DT": ["DT"],
            "LB": ["MIKE", "WILL", "SAM"], "CB": ["CB"], "S": ["FS", "SS"], "DB": ["CB"], "PK": ["K"], "P": ["P"]}
# typical last-season age by group (a vet's mean; SD CAREER_SD)
CAREER_END = {"QB": 36.5, "RB": 29.5, "WR": 31.5, "TE": 32.5, "OL": 33.0, "EDGE": 32.0, "DT": 32.0, "LB": 31.0,
              "CB": 31.0, "S": 31.5, "K": 38.0, "P": 38.0}
CAREER_SD = 2.0
# a record-book category's stat word -> the groups that can hold it (a namesake at another position isn't the holder)
RECORD_GROUPS = (("pass", {"QB"}), ("rush", {"RB", "QB", "WR", "TE"}), ("rec", {"WR", "TE", "RB"}),
                 ("sacks", {"EDGE", "DT", "LB"}), ("def_interceptions", {"CB", "S", "LB"}), ("def_tds", {"CB", "S", "LB",
                 "EDGE", "DT"}), ("tackles", {"LB", "S", "CB", "DT", "EDGE"}), ("fg", {"K"}),
                 ("scrimmage", {"RB", "WR", "TE", "QB"}))
RECORD_POSITION = (("pass", "QB"), ("rush", "HB"), ("rec", "WR"), ("sacks", "REDG"), ("def_interceptions", "CB"),
                   ("def_tds", "CB"), ("tackles", "MIKE"), ("fg", "K"), ("scrimmage", "HB"))
SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\.?$")


def name_key(name):
    """'Chris Godwin Jr.' -> 'chrisgodwin' (full names; the record book and awards use full names)."""
    return re.sub(r"[^a-z]", "", SUFFIX.sub("", (name or "").lower().strip()))


def _age(birth, season):
    """Age on Sep 1 of the season."""
    if not birth:
        return None
    y, m, d = (int(x) for x in birth[:10].split("-"))
    return season - y - (1 if (m, d) > (9, 1) else 0)


def team_path(anchors, seasons):
    """{season: team} from anchors {season: team}: each season takes the nearest anchor's team, the later one at a
    tie (a move happens halfway between two known teams) (pure)."""
    keys = sorted(anchors)
    out = {}
    for y in seasons:
        best = min(keys, key=lambda a: (abs(a - y), -a))
        out[y] = anchors[best]
    return out


def build(pool, vets, facts, seed=1):
    """pool: [{player_id, name, full_name, position, team_2035, age_2035, exp_2035, overall, dev, team_2026}];
    vets: [{espn_id, full_name, position (ESPN), team_2026, birth_date, exp_2026, production 0..1}];
    facts: [{name, season, team, kind}] (awards and record entries). Returns {members, unknown} (pure)."""
    rng = random.Random(seed)
    by_key = defaultdict(list)
    members = []
    for p in pool:
        first = 2035 - p["exp_2035"] if p.get("exp_2035") is not None else None
        if first is None or first > LAST:
            continue
        m = {"kind": "pool", "player_id": p["player_id"], "espn_id": p.get("espn_id"), "name": p["name"],
             "full_name": p["full_name"], "position": p["position"], "group": GROUP.get(p["position"]),
             "first": first, "last": LAST, "overall": p.get("overall"), "dev": p.get("dev"),
             "anchors": {2035: p["team_2035"]}, "age_2035": p.get("age_2035")}
        if p.get("team_2026") and first <= FIRST:
            m["anchors"][FIRST] = p["team_2026"]
        members.append(m)
        by_key[name_key(p["full_name"] or p["name"])].append(m)
    turn = defaultdict(int)
    for v in vets:
        opts = ESPN_POS.get(v["position"])
        if not opts:
            continue                                   # long snappers: no stats
        pos = opts[turn[v["position"]] % len(opts)]
        turn[v["position"]] += 1
        m = {"kind": "vet", "player_id": None, "espn_id": v["espn_id"], "name": _list_name(v["full_name"]),
             "full_name": v["full_name"], "position": pos, "group": GROUP[pos], "first": FIRST,
             "anchors": {FIRST: v["team_2026"]}, "birth_date": v.get("birth_date"),
             "production": v.get("production", 0.0), "exp_2026": v.get("exp_2026")}
        members.append(m)
        by_key[name_key(v["full_name"])].append(m)

    unknown = []
    for f in facts:
        groups = record_groups(f["kind"])
        cands = [m for m in by_key.get(name_key(f["name"]), []) if groups is None or m["group"] in groups]
        if len(cands) != 1:
            unknown.append(dict(f, position=record_position(f["kind"])))
            continue
        m = cands[0]
        if f.get("team"):
            m["anchors"][f["season"]] = f["team"]
        m.setdefault("fact_seasons", set()).add(f["season"])

    for m in members:
        if m["kind"] == "vet":
            age26 = _age(m["birth_date"], FIRST)
            mean = CAREER_END[m["group"]] + 2.5 * (m["production"] - 0.3)
            end_age = rng.gauss(mean, CAREER_SD)
            last = FIRST + max(0, int(round(end_age - age26))) if age26 is not None else FIRST + rng.randint(0, 3)
            if m["production"] <= 0.02 and (m.get("exp_2026") or 0) <= 1:
                last = min(last, FIRST + rng.randint(0, 2))        # a fringe 2026 player washes out soon
            last = max(last, max(m.get("fact_seasons", {FIRST})))
            m["last"] = min(LAST, last)
            m["ages"] = {y: (age26 + y - FIRST) if age26 is not None else None for y in SEASONS}
        else:
            m["ages"] = {y: (m["age_2035"] - (2035 - y)) if m.get("age_2035") else None for y in SEASONS}
        seasons = [y for y in SEASONS if max(m["first"], FIRST) <= y <= m["last"]]
        m["seasons"] = team_path(m["anchors"], seasons) if seasons else {}
        m["exp"] = {y: y - m["first"] if m["kind"] == "pool" else (m.get("exp_2026") or 0) + y - FIRST
                    for y in m["seasons"]}
        m["key"] = f"p{m['player_id']}" if m["player_id"] else f"e{m['espn_id']}"
    return {"members": [m for m in members if m["seasons"]], "unknown": unknown}


def record_position(kind):
    """The position of a fill-in who holds this record ('record:rookie_game_sacks' -> 'REDG') (pure)."""
    cat = kind.split(":", 1)[-1]
    return next((pos for word, pos in RECORD_POSITION if word in cat), "WR")


def record_groups(kind):
    """'record:rookie_game_receptions' -> {'WR', 'TE', 'RB'}; awards and unknown words -> None (pure)."""
    if not kind.startswith("record:"):
        return None
    cat = kind.split(":", 1)[1]
    for word, groups in RECORD_GROUPS:
        if word in cat:
            return groups
    return None


def _list_name(full):
    parts = SUFFIX.sub("", (full or "").strip()).split()
    return f"{parts[0][0]}. {' '.join(parts[1:])}" if len(parts) > 1 else full


# ------------------------------------------------------------------------------------------------- load ----

PRODUCTION = {          # 2025 season volume that counts as a full-time starter, by group
    "QB": ("passing_yards", 3800), "RB": ("rushing_yards", 1000), "WR": ("receiving_yards", 1000),
    "TE": ("receiving_yards", 700), "OL": ("games", 17), "EDGE": ("def_sacks", 9), "DT": ("def_tackles_solo", 30),
    "LB": ("def_tackles_solo", 80), "CB": ("def_pass_defended", 12), "S": ("def_tackles_solo", 60),
    "K": ("fg_att", 32), "P": ("pt_att", 70)}


def real_seasons(path_fmt=os.path.join(HISTORY, ".cache", "nflverse_stats_player_reg_{}.csv"), years=(2024, 2025)):
    """{gsis id: {season: row}} for the cached nflverse seasons."""
    out = defaultdict(dict)
    for y in years:
        p = path_fmt.format(y)
        if os.path.exists(p):
            with open(p) as f:
                for r in csv.DictReader(f):
                    out[r["player_id"]][y] = r
    return out


def load(cur, seed=1):
    """Reads the pool, the 2026 rosters, real 2024-2025 seasons, awards and the record book; returns build()'s result
    plus `real` ({espn id: {season: nflverse row}}) for the line step."""
    cur.execute("""SELECT p.id, p.player_name, p.full_name, p.espn_id, s.season, s.team_id, s.position, s.age,
                          s.years_of_experience, s.overall_rating, s.dev_trait
                   FROM players p JOIN player_seasons s ON s.player_id = p.id WHERE s.season IN (2035, 2036)
                   ORDER BY p.id, s.season""")
    rows = defaultdict(dict)
    for pid, pn, fn, espn, season, team, pos, age, exp, ovr, dev in cur.fetchall():
        rows[pid][season] = dict(name=pn, full_name=fn, espn_id=espn, team=team, position=pos, age=age, exp=exp,
                                 overall=ovr, dev=dev)
    with open(os.path.join(HISTORY, "rosters_2026.csv")) as f:
        rosters = list(csv.DictReader(f))
    cur.execute("SELECT id, abbreviation FROM teams")
    by_abbr = {a.strip(): i for i, a in cur.fetchall()}
    team26 = {int(r["player_id"]): by_abbr.get(r["team"]) for r in rosters if r["player_id"] and r["in_pool_2035"] == "True"}
    pool = []
    for pid, by in rows.items():
        r35 = by.get(2035) or by.get(2036)
        exp35 = r35["exp"] if 2035 in by else (by[2036]["exp"] - 1 if by[2036]["exp"] is not None else None)
        age35 = r35["age"] if 2035 in by else (by[2036]["age"] - 1 if by[2036]["age"] else None)
        if r35["position"] in ("LS", "DEF"):
            continue
        pool.append(dict(player_id=pid, name=r35["name"], full_name=r35["full_name"], espn_id=r35["espn_id"],
                         position=r35["position"], team_2035=r35["team"], age_2035=age35, exp_2035=exp35,
                         overall=r35["overall"], dev=r35["dev"], team_2026=team26.get(pid)))
    # vets: real 2026 players outside the pool, with their 2025 (else 2024) production
    with open(os.path.join(HISTORY, ".cache", "nflverse_players.csv")) as f:
        gsis = {r.get("espn_id"): r.get("gsis_id") for r in csv.DictReader(f) if r.get("espn_id")}
    real = real_seasons()
    vets, real_by_espn = [], {}
    for r in rosters:
        if r["in_pool_2035"] == "True" or r["in_pool_2036"] == "True":
            continue
        g = gsis.get(r["espn_id"])
        seasons = real.get(g, {})
        real_by_espn[r["espn_id"]] = seasons
        pos = ESPN_POS.get(r["position"], [None])[0]
        prod = 0.0
        if pos:
            col, full = PRODUCTION[GROUP[pos]]
            last = seasons.get(2025) or seasons.get(2024)
            prod = min(1.5, float(last.get(col) or 0) / full) if last else 0.0
        vets.append(dict(espn_id=r["espn_id"], full_name=r["full_name"], position=r["position"],
                         team_2026=by_abbr.get(r["team"]), birth_date=r["birth_date"],
                         exp_2026=int(r["experience_years"] or 0), production=prod))
    facts = []
    cur.execute("SELECT season, player_name, team_id, award_type FROM awards WHERE season BETWEEN %s AND %s "
                "AND award_type <> 'COTY'", (FIRST, LAST))
    facts += [dict(season=s, name=n, team=t, kind=f"award:{a}") for s, n, t, a in cur.fetchall()]
    for table in ("league_record_entries", "franchise_records"):
        cur.execute(f"SELECT season, holder, team_id, category, value FROM {table} WHERE season BETWEEN %s AND %s "
                    f"AND source = 'BOOK'", (FIRST, LAST))
        facts += [dict(season=s, name=n, team=t, kind=f"record:{c}", value=float(v))
                  for s, n, t, c, v in cur.fetchall() if "_team_" not in c]      # a team record has no player
    out = build(pool, vets, facts, seed)
    out["real"] = real_by_espn
    out["facts"] = facts
    return out
