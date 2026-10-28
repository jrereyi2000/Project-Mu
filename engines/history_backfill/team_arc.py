"""
2026-2034 team arc: standings, playoffs, points and coach lines (docs/done/HISTORY_BACKFILL_PLAN.md phase 2, §3.3;
PROJECT_SPEC §3.3j).

  python3 -m engines.history_backfill generate [--seeds 3] [--pins data/history/pins.json] [--time-limit S]
  python3 -m engines.history_backfill apply --candidate N
  python3 -m engines.history_backfill revert

Facts (hard; load_facts, check_facts):
- Coach snapshot differences 2025 -> 2034: each team's 9-season W-L, winning seasons (w > l), playoff W-L and Super
  Bowl W-L. Every team had one head coach for 2026-2034 except Carolina (Canales 2026-2033, Whitfield 2034). A coach
  with no 2034 snapshot (Canales) gets the league's remainder (every season is 272-272 and 13 playoff games), which is
  exact: Carolina 2026-2030 is forced to 18-67 with no playoff games. Bowles (TB) is adjusted (BOWLES): Madden started
  him one regular-season loss above his stored real row (his difference is 82-72, 154 games) and with no winning
  seasons (a 2025 save lists none, so his 5 by 2034 all come from 2026-2034, not 5 - 4 = 1). The AFC's coach differences add up to 64
  playoff trips, one more than 7 x 9: one AFC coach with a real pre-2026 record started in Madden one playoff loss
  above his stored row. The data can't say who, so the solver picks him (offset) and the report names him; pin him
  with "playoff_loss_offset": ["PIT"] in pins.json.
- Stored games: every playoff game 2026-2034 (each Super Bowl, Carolina's 2034 run) fixes who met whom in that round
  and who won; a team with all 17 regular-season games stored (Carolina 2034) has its record and points from them;
  other teams' stored games are lower bounds on their wins and losses.
- CANON (Panthers Full History.pdf, team_seasons): Carolina 0-17 in 2031 (alone at the bottom), 4-13 in 2032 and 2033
  (tied for the worst record), 13-4 in 2034 with a bye; no playoffs and at most 8 wins 2026-2030 (2025's 8-9 was the
  high-water mark); Chicago the No. 1 offense 2029-2033 (most points that season).
- Pins (data/history/pins.json, `{"team_seasons": [{"team": "CHI", "season": 2029, "wins": 11}]}`).

Model (solve, OR-Tools CP-SAT; CBC through PuLP found no feasible point in minutes): wins, a winning-season flag, a
playoff flag, a seed (1-7) and a round flag (lost in the Wild Card / Divisional / Conference round, or won the
conference) for every team-season. The bracket holds by seed: one Wild Card loser in each of 2v7, 3v6, 4v5, and in
the Divisional round exactly one loser between the 1 seed and the lowest seed left (7, else 6, else 5, else 4),
reseeded as the NFL does; a team's playoff wins follow from its flag and bye.
Seeds 1-4 are division winners (most wins in the division), ordered by wins; 5-7 the rest, ordered, at least as many
wins as any conference team that missed. Ties are allowed and become division_tiebreak / seeding GENERATED (no games
to break them). Objective: stay near a random strength arc per team (default_targets: an AR(1) path with the NFL's spread from
the 2026 projection to the real 2035 record, shifted to each coach's total; each candidate's seed draws different
arcs), penalize zig-zags more than steady rises and falls (into the real 2035 too), keep about five 12-win and five
5-win teams and no more than about 11 teams at 8-9 a season, and soft story terms: COTY and MVP teams win 11+ and make the playoffs (COTY teams
also improve by 3+), Super Bowl teams win 11+, playoff teams win 9+, Chicago 2029-2033 wins 10+.

finish() turns a solution into team_season_results rows (points from wins, calibrated to 2035; division places;
playoff results) and verify() re-checks every fact on the rows, independent of the solver. generate stages each
candidate (history_candidates, history_team_seasons) and writes verify_output/history_team_arc.md for JR's pick;
apply copies one into league_seasons, team_season_results and coach_season_lines with source SYNTH; revert deletes
them. Nothing outside the staging tables is written before apply.
"""
import itertools
import json
import math
import os
import random
from collections import defaultdict

import psycopg
from ortools.sat.python import cp_model

CONN_INFO = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")
ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
START_CSV = os.path.join(ROOT, "data", "history", "start_2026.csv")
PINS_JSON = os.path.join(ROOT, "data", "history", "pins.json")
REPORT_MD = os.path.join(ROOT, "verify_output", "history_team_arc.md")

FIRST, LAST = 2026, 2034
SEASONS = list(range(FIRST, LAST + 1))
GAMES = 17
TEAMS = 32
SEASON_WINS = TEAMS * GAMES // 2              # 272
PLAYOFF_GAMES = 13
SEEDS = 7
WORKERS = 8
GENERATED = "GENERATED"
WC_PAIRS = ((2, 7), (3, 6), (4, 5))
ROUND_FLAGS = ("WC", "DIV", "CONF", "CC")
LOST_IN = {"WC": "WILD_CARD", "DIV": "DIVISIONAL", "CONF": "CONFERENCE"}

# Madden started Todd Bowles (coach 38) away from his stored real 2025 row (PROJECT_SPEC §3.3a; plan §1, B8): one
# more loss, and no winning seasons (JR checked a 2025 save, 2026-10-26: Madden lists none before 2026, so all five
# of his 2034 count are 2026-2034; his stored row's 4 is real-life history).
BOWLES = {38: {"losses": -1, "winning_seasons": 4}}

# Panthers Full History.pdf and team_seasons (plan §1: numbers it states are hard).
CANON = {
    "records": {("CAR", 2031): 0, ("CAR", 2032): 4, ("CAR", 2033): 4, ("CAR", 2034): 13},
    "max_wins": {("CAR", y): 8 for y in range(2026, 2031)},
    "no_playoffs": {("CAR", y) for y in range(2026, 2034)},
    "seed1": {("CAR", 2034)},
    "worst_alone": {2031: "CAR"},
    "worst_tied": {2032: "CAR", 2033: "CAR"},
    "top_offense": {2029: "CHI", 2030: "CHI", 2031: "CHI", 2032: "CHI", 2033: "CHI"},
}

# objective weights
W_TARGET, W_TARGET_2026, W_SWING, W_ZIGZAG = 1.0, 1.5, 0.05, 0.25
STORY = {"COTY": (1.0, 4.0), "MVP": (1.0, 3.0)}       # (per win short of 11, missing the playoffs)
W_COTY_JUMP, W_SB_SHORT, W_TOP_OFFENSE, W_WEAK_PLAYOFF = 0.5, 2.0, 0.8, 1.0
ARC_SD, ARC_RHO, ARC_STRETCH = 3.0, 0.65, 1.2              # team strength arcs (default_targets)
SPREAD_TOP, SPREAD_BOTTOM, W_SPREAD = 5, 5, 3.0            # per season: about five 12+ and five 5- win teams (2035)
MIDDLE_CAP, W_MIDDLE = 11, 2.0                              # and at most about 11 teams at 8-9 (2035: 12)

# points (2035 team_season_results: PD = 31.5 x (W - 8.5), PF mean 416; real 2025 NFL about 383)
PD_PER_WIN, PD_SD, PF_SD = 31.5, 35.0, 28.0
PF_2026, PF_2035 = 383.0, 416.0


# ---------------------------------------------------------------------------------------------- bracket ----

def conference_outcomes():
    """The 64 ways a conference's playoffs can go, by seed. Each: {wins {seed: games won}, lost {seed: round},
    champ seed, games [(round, winner seed, loser seed)]} (pure)."""
    out = []
    for wc in itertools.product((True, False), repeat=3):
        wins, lost, games = {k: 0 for k in range(1, SEEDS + 1)}, {}, []
        adv = [1]
        for hi_wins, (hi, lo) in zip(wc, WC_PAIRS):
            w, l = (hi, lo) if hi_wins else (lo, hi)
            wins[w] += 1
            lost[l] = "WILD_CARD"
            games.append(("WILD_CARD", w, l))
            adv.append(w)
        adv.sort()
        div_games = ((adv[0], adv[3]), (adv[1], adv[2]))       # reseeded: the 1 seed hosts the lowest left
        for dv in itertools.product((True, False), repeat=2):
            w2, l2, g2 = dict(wins), dict(lost), list(games)
            conf = []
            for hi_wins, (hi, lo) in zip(dv, div_games):
                w, l = (hi, lo) if hi_wins else (lo, hi)
                w2[w] += 1
                l2[l] = "DIVISIONAL"
                g2.append(("DIVISIONAL", w, l))
                conf.append(w)
            hi, lo = sorted(conf)
            for hi_wins in (True, False):
                w, l = (hi, lo) if hi_wins else (lo, hi)
                w3, l3 = dict(w2), dict(l2)
                w3[w] += 1
                l3[l] = "CONFERENCE"
                out.append({"wins": w3, "lost": l3, "champ": w, "games": g2 + [("CONFERENCE", w, l)]})
    return out


OUTCOMES = conference_outcomes()


# ------------------------------------------------------------------------------------------------ facts ----

def load_facts(cur):
    """Everything the model needs from the database, as plain data (see check_facts for what it must satisfy)."""
    cur.execute("SELECT id, abbreviation, conference, division FROM teams ORDER BY id")
    teams = {r[0]: {"abbr": r[1].strip(), "conf": r[2].strip(), "div": r[3].strip()} for r in cur.fetchall()}
    by_abbr = {t["abbr"]: tid for tid, t in teams.items()}

    cur.execute("""SELECT coach_id, team_id, start_season, end_season FROM coach_tenures
                   WHERE start_season <= %s AND coalesce(end_season, 9999) >= %s""", (LAST, FIRST))
    coach = {}
    for cid, tid, start, end in cur.fetchall():
        for y in SEASONS:
            if start <= y <= (end or 9999):
                coach[(tid, y)] = cid

    cols = ("career_wins", "career_losses", "winning_seasons", "playoff_wins", "playoff_losses", "super_bowl_wins",
            "super_bowl_losses")
    cur.execute(f"SELECT coach_id, season, {', '.join(cols)} FROM coach_seasons WHERE season IN (%s, %s)",
                (FIRST - 1, LAST))
    snap = {(r[0], r[1]): dict(zip(("wins", "losses", "winning_seasons", "playoff_wins", "playoff_losses",
                                    "sb_wins", "sb_losses"), r[2:])) for r in cur.fetchall()}

    cur.execute("""SELECT id, season, game_type, home_team_id, away_team_id, home_score, away_score FROM games
                   WHERE season BETWEEN %s AND %s AND status = 'COMPLETED' AND home_score IS NOT NULL""", (FIRST, LAST))
    games = [dict(zip(("id", "season", "type", "home", "away", "hs", "as"), r)) for r in cur.fetchall()]

    cur.execute("SELECT season, award_type, team_id FROM awards WHERE season BETWEEN %s AND %s AND team_id IS NOT NULL",
                (FIRST, LAST))
    awards = [(s, a, t) for s, a, t in cur.fetchall() if a in ("COTY", "MVP")]

    cur.execute("SELECT team_id, wins FROM team_season_results WHERE season = %s", (LAST + 1,))
    end = dict(cur.fetchall())
    gms = []
    if _has(cur, "gm_profiles"):
        cur.execute("SELECT id, team_id, start_season, end_season FROM gm_profiles")
        gms = cur.fetchall()
    return {"teams": teams, "by_abbr": by_abbr, "coach": coach, "snap": snap, "games": games, "awards": awards,
            "start": read_start(by_abbr), "end": end, "gms": gms}


def _has(cur, table):
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (table,))
    return cur.fetchone()[0]


def read_start(by_abbr, path=START_CSV):
    import csv
    with open(path) as f:
        return {by_abbr[r["team"]]: float(r["proj_wins"]) for r in csv.DictReader(f) if r["team"] in by_abbr}


def derive(facts, canon_facts=CANON, adjust=BOWLES):
    """From the raw facts: groups (one per coach tenure, with exact totals), fixed records and points, playoff games,
    Super Bowls and canon in team ids. canon_facts / adjust default to CANON / BOWLES. Returns (derived, problems)
    (pure)."""
    teams, by_abbr = facts["teams"], facts["by_abbr"]
    problems = []
    tenure = defaultdict(list)
    for (tid, y), cid in facts["coach"].items():
        tenure[(cid, tid)].append(y)
    for tid in teams:
        for y in SEASONS:
            if (tid, y) not in facts["coach"]:
                problems.append(f"{teams[tid]['abbr']} {y}: no head coach on record")

    keys = ("wins", "losses", "winning_seasons", "playoff_wins", "playoff_losses", "sb_wins", "sb_losses")
    groups, open_groups = [], []
    for (cid, tid), ys in sorted(tenure.items()):
        end = facts["snap"].get((cid, LAST))
        if end is None or max(ys) != LAST:
            open_groups.append({"coach": cid, "team": tid, "seasons": sorted(ys)})
            continue
        base = facts["snap"].get((cid, FIRST - 1), dict.fromkeys(keys, 0))
        tot = {k: (end[k] or 0) - (base[k] or 0) for k in keys}
        for k, d in adjust.get(cid, {}).items():
            tot[k] += d
        if min(ys) != FIRST and (cid, FIRST - 1) in facts["snap"]:
            problems.append(f"coach {cid}: tenure starts {min(ys)} but the snapshot difference starts at {FIRST - 1}")
        groups.append({"coach": cid, "team": tid, "seasons": sorted(ys), **tot})
    league = {"wins": SEASON_WINS * len(SEASONS), "losses": SEASON_WINS * len(SEASONS),
              "playoff_wins": PLAYOFF_GAMES * len(SEASONS), "playoff_losses": PLAYOFF_GAMES * len(SEASONS),
              "sb_wins": len(SEASONS), "sb_losses": len(SEASONS)}
    if len(open_groups) == 1:          # the one coach without a closing snapshot takes the league's remainder
        g = open_groups[0]
        rest = {k: league[k] - sum(x[k] for x in groups) for k in league}
        # a conference without him whose trips run over 7 a season holds playoff losses that aren't real (below):
        # they aren't his to absorb
        mine = teams[g["team"]]["conf"]
        for c in {t["conf"] for t in teams.values()} - {mine}:
            trips = sum(x["playoff_losses"] + x["sb_wins"] for x in groups if teams[x["team"]]["conf"] == c)
            rest["playoff_losses"] += max(0, trips - SEEDS * len(SEASONS))
        groups.append({**g, **rest, "winning_seasons": None, "derived": True})
    else:
        for g in open_groups:
            groups.append({**g, **dict.fromkeys(keys), "derived": True})
        if open_groups:
            problems.append(f"{len(open_groups)} coaches without a {LAST} snapshot: their totals are free")
    for g in groups:
        if g["wins"] is not None and g["wins"] + g["losses"] != GAMES * len(g["seasons"]):
            problems.append(f"coach {g['coach']} ({teams[g['team']]['abbr']}): {g['wins']}-{g['losses']} over "
                            f"{len(g['seasons'])} seasons")
        if g.get("winning_seasons") is not None:
            n = len(g["seasons"])
            ws = g["winning_seasons"]
            if not (9 * ws <= g["wins"] <= 8 * (n - ws) + GAMES * ws):
                problems.append(f"coach {g['coach']} ({teams[g['team']]['abbr']}): {g['wins']} wins can't come with "
                                f"{ws} winning seasons in {n}")

    # stored games
    fixed, lower, upper, pts = {}, defaultdict(int), defaultdict(int), {}
    reg = defaultdict(list)
    playoff_games, sb = [], {}
    for g in facts["games"]:
        win, lose = (g["home"], g["away"]) if g["hs"] > g["as"] else (g["away"], g["home"])
        if g["type"] == "REGULAR_SEASON":
            for t, mine, theirs in ((g["home"], g["hs"], g["as"]), (g["away"], g["as"], g["hs"])):
                reg[(t, g["season"])].append((mine, theirs))
        elif g["type"] == "SUPER_BOWL":
            sb[g["season"]] = (win, lose, g["id"], g)
        else:
            playoff_games.append({"season": g["season"], "round": g["type"], "winner": win, "loser": lose})
    for key, lines in reg.items():
        w = sum(1 for m, t in lines if m > t)
        l = sum(1 for m, t in lines if m < t)
        if len(lines) == GAMES:
            fixed[key] = w
            pts[key] = (sum(m for m, _ in lines), sum(t for _, t in lines))
        else:
            lower[key], upper[key] = w, GAMES - l
    for y in SEASONS:
        if y not in sb:
            problems.append(f"{y}: no Super Bowl game stored")

    canon = {"records": {}, "max_wins": {}, "no_playoffs": set(), "seed1": set(), "worst_alone": {},
             "worst_tied": {}, "top_offense": {}}
    for (ab, y), w in canon_facts.get("records", {}).items():
        canon["records"][(by_abbr[ab], y)] = w
    for (ab, y), w in canon_facts.get("max_wins", {}).items():
        canon["max_wins"][(by_abbr[ab], y)] = w
    canon["no_playoffs"] = {(by_abbr[ab], y) for ab, y in canon_facts.get("no_playoffs", ())}
    canon["seed1"] = {(by_abbr[ab], y) for ab, y in canon_facts.get("seed1", ())}
    for k in ("worst_alone", "worst_tied", "top_offense"):
        canon[k] = {y: by_abbr[ab] for y, ab in canon_facts.get(k, {}).items()}
    for key, w in canon["records"].items():
        if key in fixed and fixed[key] != w:
            problems.append(f"{teams[key[0]]['abbr']} {key[1]}: canon {w} wins, stored games {fixed[key]}")
        fixed[key] = w

    # league sums close exactly (every coach's totals are known)
    for k in ("wins", "playoff_wins", "sb_wins", "sb_losses"):
        tot = sum(g[k] or 0 for g in groups)
        want = {"wins": SEASON_WINS, "playoff_wins": PLAYOFF_GAMES, "playoff_losses": PLAYOFF_GAMES,
                "sb_wins": 1, "sb_losses": 1}[k] * len(SEASONS)
        if all(g[k] is not None for g in groups) and tot != want:
            problems.append(f"coach totals: {k} sum to {tot}, the league needs {want}")
    # playoff trips per conference: 7 a season. An excess is a coach whose Madden start had a playoff loss his stored
    # 2025 row lacks; only a coach with a real pre-2026 record (a non-zero 2025 row) can carry it.
    excess, candidates = {}, {}
    for c in sorted({t["conf"] for t in teams.values()}):
        gs = [i for i, g in enumerate(groups) if teams[g["team"]]["conf"] == c]
        if any(groups[i]["playoff_losses"] is None for i in gs):
            continue
        trips = sum(groups[i]["playoff_losses"] + groups[i]["sb_wins"] for i in gs)
        excess[c] = trips - SEEDS * len(SEASONS)
        base = lambda i: facts["snap"].get((groups[i]["coach"], FIRST - 1)) or {}
        candidates[c] = [i for i in gs if groups[i]["playoff_losses"] > 0
                         and (base(i).get("wins") or 0) + (base(i).get("losses") or 0) > 0]
        if excess[c] < 0 or excess[c] > len(candidates[c]):
            problems.append(f"{c}: coach differences give {trips} playoff trips, the league has "
                            f"{SEEDS * len(SEASONS)}")
    return {"groups": groups, "fixed": fixed, "excess": excess, "offset_candidates": candidates, "lower": dict(lower), "upper": dict(upper), "points": pts,
            "playoff_games": playoff_games, "sb": sb, "canon": canon}, problems


# ---------------------------------------------------------------------------------------------- targets ----

def default_targets(facts, d, seed):
    """{(team, season): target wins}: a random strength arc per team, drawn from `seed` (pure). Each arc is an AR(1)
    path with the NFL's spread (stationary SD ARC_SD, year-to-year correlation ARC_RHO) from the 2026 projection,
    stretched to outcome spread (ARC_STRETCH: projected wins are expected values, SD 2.4 against 2035's 2.85), bent
    to land on the real 2035 record (the AR(1) bridge: each season moves by its covariance share of the miss at
    2035), then shifted so each coach group's free seasons add up to its total. Unlike a path pulled toward .500, the
    middle seasons keep good and bad teams."""
    rng = random.Random(seed)
    tgt = {}
    T = LAST + 1
    noise = ARC_SD * math.sqrt(1 - ARC_RHO ** 2)
    for tid in facts["teams"]:
        a = 8.5 + (facts["start"].get(tid, 8.5) - 8.5) * ARC_STRETCH
        b = facts["end"].get(tid, 8.5)
        path = {FIRST: a}
        for y in range(FIRST + 1, T + 1):
            path[y] = 8.5 + ARC_RHO * (path[y - 1] - 8.5) + rng.gauss(0, noise)
        miss = b - path[T]
        for y in SEASONS:
            share = ARC_RHO ** (T - y) * (1 - ARC_RHO ** (2 * (y - FIRST))) / (1 - ARC_RHO ** (2 * (T - FIRST)))
            tgt[(tid, y)] = path[y] + share * miss
    for g in d["groups"]:
        free = [y for y in g["seasons"] if (g["team"], y) not in d["fixed"]]
        if g["wins"] is None or not free:
            continue
        want = g["wins"] - sum(d["fixed"][(g["team"], y)] for y in g["seasons"] if (g["team"], y) in d["fixed"])
        shift = (want - sum(tgt[(g["team"], y)] for y in free)) / len(free)
        for y in free:
            tgt[(g["team"], y)] = min(16.0, max(1.0, tgt[(g["team"], y)] + shift))
    return tgt


# ------------------------------------------------------------------------------------------------ model ----

FAMILIES = ("coach_totals", "stored_games", "canon", "pins")
OFFSET_KEY = "playoff_loss_offset"
SCALE = 10                    # objective in tenths of a win; weights x 10 so every coefficient is an integer


def build(facts, d, targets, pins=(), drop=()):
    """The CP-SAT model. Returns (model, vars). drop: constraint families left out (infeasibility diagnosis)."""
    offset_pins = [t for pin in pins for t in pin.get(OFFSET_KEY, [])]
    teams = facts["teams"]
    T = sorted(teams)
    confs = sorted({t["conf"] for t in teams.values()})
    divs = defaultdict(list)
    for tid, t in teams.items():
        divs[t["div"]].append(tid)
    in_conf = {c: [t for t in T if teams[t]["conf"] == c] for c in confs}
    K = range(1, SEEDS + 1)
    m = cp_model.CpModel()
    B = m.NewBoolVar
    w = {(t, y): m.NewIntVar(0, GAMES, f"w_{t}_{y}") for t in T for y in SEASONS}
    ws = {k: B(f"ws_{k[0]}_{k[1]}") for k in w}
    a = {k: B(f"a_{k[0]}_{k[1]}") for k in w}
    x = {(t, y, k): B(f"x_{t}_{y}_{k}") for t in T for y in SEASONS for k in K}
    # round flags: lost in the Wild Card / Divisional / Conference round, or won the conference
    r = {(t, y, n): B(f"r_{t}_{y}_{n}") for t in T for y in SEASONS for n in ROUND_FLAGS}
    sb_win = {(t, y): int(d["sb"][y][0] == t) for t in T for y in SEASONS if y in d["sb"]}
    lowest = {}

    for y in SEASONS:
        m.Add(sum(w[(t, y)] for t in T) == SEASON_WINS)
        for t in T:
            m.Add(w[(t, y)] >= 9).OnlyEnforceIf(ws[(t, y)])
            m.Add(w[(t, y)] <= 8).OnlyEnforceIf(ws[(t, y)].Not())
            m.Add(sum(x[(t, y, k)] for k in K) == a[(t, y)])
            m.Add(sum(r[(t, y, n)] for n in ROUND_FLAGS) == a[(t, y)])
            m.AddImplication(x[(t, y, 1)], r[(t, y, "WC")].Not())          # the 1 seed has a bye
        for dv, members in divs.items():
            top = m.NewIntVar(0, GAMES, f"divmax_{dv}_{y}")
            m.AddMaxEquality(top, [w[(t, y)] for t in members])
            dwin = {}
            for t in members:
                dwin[t] = B(f"dw_{t}_{y}")
                m.Add(sum(x[(t, y, k)] for k in range(1, 5)) == dwin[t])
                m.Add(w[(t, y)] == top).OnlyEnforceIf(dwin[t])
            m.AddExactlyOne(dwin.values())
        for c in confs:
            members = in_conf[c]
            slot = {}
            for k in K:
                m.AddExactlyOne(x[(t, y, k)] for t in members)
                slot[k] = m.NewIntVar(0, GAMES, f"slot_{c}_{y}_{k}")
                for t in members:
                    m.Add(slot[k] == w[(t, y)]).OnlyEnforceIf(x[(t, y, k)])
            for k in (1, 2, 3, 5, 6):
                m.Add(slot[k] >= slot[k + 1])
            for j in members:
                m.Add(slot[7] >= w[(j, y)]).OnlyEnforceIf(a[(j, y)].Not())
            for n, cnt in (("WC", 3), ("DIV", 2), ("CONF", 1), ("CC", 1)):
                m.Add(sum(r[(t, y, n)] for t in members) == cnt)
            # Wild Card: one loser in each of 2v7, 3v6, 4v5
            lost_wc, lost_div = {}, {}
            for k in K:
                lw = []
                for t in members:
                    b = B(f"lw_{t}_{y}_{k}")
                    m.AddBoolAnd([x[(t, y, k)], r[(t, y, "WC")]]).OnlyEnforceIf(b)
                    m.AddBoolOr([x[(t, y, k)].Not(), r[(t, y, "WC")].Not(), b])
                    lw.append(b)
                lost_wc[k] = B(f"lostwc_{c}_{y}_{k}")
                m.Add(sum(lw) == lost_wc[k])
                ld = []
                for t in members:
                    b = B(f"ld_{t}_{y}_{k}")
                    m.AddBoolAnd([x[(t, y, k)], r[(t, y, "DIV")]]).OnlyEnforceIf(b)
                    m.AddBoolOr([x[(t, y, k)].Not(), r[(t, y, "DIV")].Not(), b])
                    ld.append(b)
                lost_div[k] = B(f"lostdiv_{c}_{y}_{k}")
                m.Add(sum(ld) == lost_div[k])
            m.Add(lost_wc[1] == 0)
            for hi, lo in WC_PAIRS:
                m.Add(lost_wc[hi] + lost_wc[lo] == 1)
            # Divisional: the 1 seed hosts the lowest seed left (7 if it won, else 6, else 5, else 4); one of them loses
            lam = {k: B(f"low_{c}_{y}_{k}") for k in (4, 5, 6, 7)}
            m.Add(lam[7] == lost_wc[7].Not())
            m.AddBoolAnd([lost_wc[6].Not(), lost_wc[7]]).OnlyEnforceIf(lam[6])
            m.AddBoolOr([lost_wc[6], lost_wc[7].Not(), lam[6]])
            m.AddBoolAnd([lost_wc[5].Not(), lost_wc[6], lost_wc[7]]).OnlyEnforceIf(lam[5])
            m.AddBoolOr([lost_wc[5], lost_wc[6].Not(), lost_wc[7].Not(), lam[5]])
            m.AddExactlyOne(lam.values())
            lowest[(c, y)] = lam
            pair = []
            for k in (4, 5, 6, 7):
                e = B(f"lowlost_{c}_{y}_{k}")
                m.AddBoolAnd([lam[k], lost_div[k]]).OnlyEnforceIf(e)
                m.AddBoolOr([lam[k].Not(), lost_div[k].Not(), e])
                pair.append(e)
            m.Add(lost_div[1] + sum(pair) == 1)
            # the stored Super Bowl team of this conference won it
            if y in d["sb"]:
                for team in d["sb"][y][:2]:
                    if teams[team]["conf"] == c:
                        m.Add(r[(team, y, "CC")] == 1)

    # stored playoff games: that round, these two teams, this winner
    if "stored_games" not in drop:
        for n, g in enumerate(d["playoff_games"]):
            y, A, Bt = g["season"], g["winner"], g["loser"]
            lost = {"WILD_CARD": "WC", "DIVISIONAL": "DIV", "CONFERENCE": "CONF"}[g["round"]]
            m.Add(r[(Bt, y, lost)] == 1)
            m.Add(sum(r[(A, y, f)] for f in ROUND_FLAGS[ROUND_FLAGS.index(lost) + 1:]) == 1)
            if g["round"] == "DIVISIONAL" and (A, y) in d["canon"]["seed1"]:
                for k, lk in lowest[(teams[A]["conf"], y)].items():
                    m.AddImplication(lk, x[(Bt, y, k)])                     # the 1 seed met the lowest seed left
        for key, lo in d["lower"].items():
            if key in w:
                m.Add(w[key] >= lo)
        for key, hi in d["upper"].items():
            if key in w:
                m.Add(w[key] <= hi)
    for key, val in d["fixed"].items():
        kept = (key in d["points"] and "stored_games" not in drop) or \
            (key in d["canon"]["records"] and "canon" not in drop)
        if key in w and kept:
            m.Add(w[key] == val)

    off = {}
    for c, n in d["excess"].items():
        if n:
            for i in d["offset_candidates"][c]:
                off[i] = B(f"offset_{i}")
            m.Add(sum(off[i] for i in d["offset_candidates"][c]) == n)
    if "pins" not in drop and offset_pins:
        for i, var in off.items():
            m.Add(var == int(d["groups"][i]["team"] in offset_pins))
    if "coach_totals" not in drop:
        for i, g in enumerate(d["groups"]):
            ys = [(g["team"], y) for y in g["seasons"]]
            if g["wins"] is not None:
                m.Add(sum(w[k] for k in ys) == g["wins"])
            if g.get("winning_seasons") is not None:
                m.Add(sum(ws[k] for k in ys) == g["winning_seasons"])
            if g["playoff_wins"] is not None:
                # games won: Divisional loser 1, Conference loser 2, conference winner 3; a bye is one fewer
                m.Add(sum(r[(*k, "DIV")] + 2 * r[(*k, "CONF")] + 3 * r[(*k, "CC")] - x[(*k, 1)] for k in ys)
                      + sum(sb_win.get(k, 0) for k in ys) == g["playoff_wins"])
                m.Add(sum(a[k] for k in ys) - sum(sb_win.get(k, 0) for k in ys)
                      == g["playoff_losses"] - (off[i] if i in off else 0))

    cn = d["canon"]
    if "canon" not in drop:
        for key, hi in cn["max_wins"].items():
            m.Add(w[key] <= hi)
        for key in cn["no_playoffs"]:
            m.Add(a[key] == 0)
        for (t, y) in cn["seed1"]:
            m.Add(x[(t, y, 1)] == 1)
        for y, t in cn["worst_alone"].items():
            for j in T:
                if j != t:
                    m.Add(w[(j, y)] >= w[(t, y)] + 1)
        for y, t in cn["worst_tied"].items():
            tied = []
            for j in T:
                if j != t:
                    m.Add(w[(j, y)] >= w[(t, y)])
                    e = B(f"tied_{y}_{j}")
                    m.Add(w[(j, y)] == w[(t, y)]).OnlyEnforceIf(e)
                    tied.append(e)
            m.AddBoolOr(tied)
    if "pins" not in drop:
        for pin in pins:
            if "team" not in pin:
                continue
            if "wins" in pin:
                m.Add(w[(pin["team"], pin["season"])] == pin["wins"])
            if "playoffs" in pin:
                m.Add(a[(pin["team"], pin["season"])] == int(bool(pin["playoffs"])))

    # objective, in tenths of a win
    obj = []
    def wt(weight):
        return int(round(weight * SCALE))
    for (t, y), tg in targets.items():
        dev = m.NewIntVar(0, GAMES * SCALE, f"dev_{t}_{y}")
        m.AddAbsEquality(dev, _diff(m, SCALE * w[(t, y)] - int(round(tg * SCALE)), GAMES * SCALE, f"devd_{t}_{y}"))
        obj.append(wt(W_TARGET_2026 if y == FIRST else W_TARGET) * dev)
    for t in T:
        for y in SEASONS[1:] + [LAST + 1]:
            nxt = w[(t, y)] if y <= LAST else facts["end"].get(t)
            if nxt is None:
                continue
            sw = m.NewIntVar(0, GAMES, f"sw_{t}_{y}")
            m.AddAbsEquality(sw, _diff(m, nxt - w[(t, y - 1)], GAMES, f"swd_{t}_{y}"))
            obj.append(wt(W_SWING) * SCALE * sw)
        for y in SEASONS[1:]:                          # a zig-zag (up then down) costs more than a steady rise or fall
            nxt = w[(t, y + 1)] if y < LAST else facts["end"].get(t)
            if nxt is None:
                continue
            zz = m.NewIntVar(0, 2 * GAMES, f"zz_{t}_{y}")
            m.AddAbsEquality(zz, _diff(m, nxt - 2 * w[(t, y)] + w[(t, y - 1)], 2 * GAMES, f"zzd_{t}_{y}"))
            obj.append(wt(W_ZIGZAG) * SCALE * zz)
    for y in SEASONS:                                  # keep real good and bad teams every season
        hi, lo, mid = [], [], []
        for t in T:
            h, l, md = B(f"hi_{t}_{y}"), B(f"lo_{t}_{y}"), B(f"mid_{t}_{y}")
            m.Add(w[(t, y)] >= 12).OnlyEnforceIf(h)
            m.Add(w[(t, y)] <= 5).OnlyEnforceIf(l)
            m.AddLinearConstraint(w[(t, y)], 8, 9).OnlyEnforceIf(md)
            m.AddLinearExpressionInDomain(w[(t, y)], cp_model.Domain.FromIntervals([[0, 7], [10, GAMES]])) \
                .OnlyEnforceIf(md.Not())
            hi.append(h)
            lo.append(l)
            mid.append(md)
        crowd = m.NewIntVar(0, TEAMS, f"crowd_{y}")
        m.Add(crowd >= sum(mid) - MIDDLE_CAP)
        obj.append(wt(W_MIDDLE) * SCALE * crowd)
        for name, flags, want in (("top", hi, SPREAD_TOP), ("bottom", lo, SPREAD_BOTTOM)):
            short = m.NewIntVar(0, want, f"spread_{name}_{y}")
            m.Add(short >= want - sum(flags))
            obj.append(wt(W_SPREAD) * SCALE * short)
    for n, (y, award, t) in enumerate(facts["awards"]):
        short_w, miss_w = STORY[award]
        sh = m.NewIntVar(0, 11, f"short_{n}")
        m.Add(sh >= 11 - w[(t, y)])
        obj += [wt(short_w) * SCALE * sh, wt(miss_w) * SCALE * (1 - a[(t, y)])]
        if award == "COTY":
            jump = m.NewIntVar(0, 20, f"jump_{n}")
            prev = w[(t, y - 1)] if y > FIRST else int(round(facts["start"].get(t, 8.5)))
            m.Add(jump >= 3 - (w[(t, y)] - prev))
            obj.append(wt(W_COTY_JUMP) * SCALE * jump)
    for y, (win, lose, *_r) in d["sb"].items():
        for t in (win, lose):
            sh = m.NewIntVar(0, 11, f"sbshort_{y}_{t}")
            m.Add(sh >= 11 - w[(t, y)])
            obj.append(wt(W_SB_SHORT) * SCALE * sh)
    for (t, y), at in a.items():                       # a playoff team under 9 wins (some coach totals force a few)
        sh = m.NewIntVar(0, 9, f"weak_{t}_{y}")
        m.Add(sh >= 9 - w[(t, y)]).OnlyEnforceIf(at)
        obj.append(wt(W_WEAK_PLAYOFF) * SCALE * sh)
    for y, t in cn["top_offense"].items():
        sh = m.NewIntVar(0, 10, f"offshort_{y}_{t}")
        m.Add(sh >= 10 - w[(t, y)])
        obj.append(wt(W_TOP_OFFENSE) * SCALE * sh)
    m.Minimize(sum(obj))
    return m, {"w": w, "a": a, "x": x, "r": r, "offset": off}


def _diff(m, expr, bound, name):
    """expr as its own variable: OR-Tools 9.12's AddAbsEquality mishandles an expression with a constant offset
    (|17 - w| with w = 6 comes back infeasible)."""
    v = m.NewIntVar(-bound, bound, name)
    m.Add(v == expr)
    return v


def solve(facts, d, targets, pins=(), time_limit=180, drop=(), seed=0):
    """Returns (solution or None, status, objective). solution: {w {(t,y): wins}, seed {(t,y): k}, round
    {(t,y): flag}, offset [group index]}."""
    model, var = build(facts, d, targets, pins, drop)
    s = cp_model.CpSolver()
    s.parameters.max_time_in_seconds = time_limit
    s.parameters.num_workers = WORKERS
    s.parameters.random_seed = seed
    s.parameters.relative_gap_limit = 0.01
    code = s.Solve(model)
    status = s.StatusName(code)
    if code not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, status, None
    sol = {"w": {k: s.Value(v) for k, v in var["w"].items()},
           "seed": {(t, y): k for (t, y, k), v in var["x"].items() if s.Value(v)},
           "round": {(t, y): n for (t, y, n), v in var["r"].items() if s.Value(v)},
           "offset": sorted(i for i, v in var["offset"].items() if s.Value(v))}
    return sol, status, s.ObjectiveValue() / (SCALE * SCALE)


def diagnose(facts, d, targets, pins=(), time_limit=60):
    """Which constraint families, left out one at a time, make the model feasible."""
    fixes = []
    for fam in FAMILIES:
        sol, status, _ = solve(facts, d, targets, pins, time_limit, drop=(fam,))
        if sol is not None:
            fixes.append(fam)
    return fixes


# ----------------------------------------------------------------------------------------------- finish ----

def finish(facts, d, sol, seed):
    """team_season_results rows {(team, season): row} from a solution: playoff results, division places, points."""
    rng = random.Random(10_000 + seed)
    teams = facts["teams"]
    rows = {}
    for (t, y), wins in sol["w"].items():
        rows[(t, y)] = {"season": y, "team_id": t, "wins": wins, "losses": GAMES - wins, "ties": 0,
                        "conference_seed": sol["seed"].get((t, y)), "playoff_result": "MISSED",
                        "playoff_wins": 0, "playoff_losses": 0, "division_tiebreak": None,
                        "head_coach_id": facts["coach"].get((t, y)), "gm_id": gm_for(facts["gms"], t, y)}
    # playoffs
    for (t, y), n in sol["round"].items():
        rw = rows[(t, y)]
        bye = rw["conference_seed"] == 1
        if n == "CC":
            win = d["sb"][y][0]
            rw["playoff_result"] = "CHAMPION" if t == win else "SUPER_BOWL_LOSS"
            rw["playoff_wins"] = 3 - bye + int(t == win)
            rw["playoff_losses"] = int(t != win)
        else:
            rw["playoff_result"] = LOST_IN[n]
            rw["playoff_wins"] = {"WC": 0, "DIV": 1, "CONF": 2}[n] - bye
            rw["playoff_losses"] = 1
    # division places
    divs = defaultdict(list)
    for (t, y), r in rows.items():
        divs[(teams[t]["div"], y)].append(r)
    for _, rs in divs.items():
        rng.shuffle(rs)
        rs.sort(key=lambda r: (-r["wins"], 0 if (r["conference_seed"] or 9) <= 4 else 1))
        for i, r in enumerate(rs):
            r["division_place"] = i + 1
            if any(o is not r and o["wins"] == r["wins"] for o in rs):
                r["division_tiebreak"] = GENERATED
    # points
    for y in SEASONS:
        frac = (y - FIRST) / (LAST + 1 - FIRST)
        mean_pf = PF_2026 + (PF_2035 - PF_2026) * frac
        free = [t for t in teams if (t, y) not in d["points"]]
        pd = {t: PD_PER_WIN * (rows[(t, y)]["wins"] - 8.5) + rng.gauss(0, PD_SD) for t in free}
        fixed_pd = sum(pf - pa for (t, yy), (pf, pa) in d["points"].items() if yy == y)
        shift = (fixed_pd + sum(pd.values())) / len(free)
        for t in free:
            pd[t] -= shift
            pf = mean_pf + pd[t] / 2 + rng.gauss(0, PF_SD)
            rows[(t, y)]["points_for"] = int(round(pf))
            rows[(t, y)]["points_against"] = int(round(pf - pd[t]))
        for (t, yy), (pf, pa) in d["points"].items():
            if yy == y:
                rows[(t, y)]["points_for"], rows[(t, y)]["points_against"] = pf, pa
        _balance(rows, y, free)
        top = d["canon"]["top_offense"].get(y)
        if top is not None:
            best = max(rows[(t, y)]["points_for"] for t in teams if t != top)
            r = rows[(top, y)]
            if r["points_for"] <= best:
                bump = best - r["points_for"] + rng.randint(6, 24)
                r["points_for"] += bump
                r["points_against"] += bump                  # same differential; the league total stays balanced
    return rows


def _balance(rows, y, free):
    """Points for and against must add up to the same league total; round-off goes to one free team."""
    diff = sum(r["points_for"] - r["points_against"] for (t, yy), r in rows.items() if yy == y)
    if diff and free:
        rows[(free[0], y)]["points_against"] += diff


def gm_for(gms, team, season):
    best = None
    for gid, tid, start, end in gms:
        if tid == team and start is not None and start <= season and (end is None or season <= end):
            if best is None or start > best[0]:
                best = (start, gid)
    return best[1] if best else None


# ----------------------------------------------------------------------------------------------- verify ----

def verify(facts, d, rows, pins=(), offset=()):
    """Every fact re-checked on the finished rows, independent of the solver. offset: the groups (indexes) carrying a
    conference's extra playoff loss. Returns [problem] (pure)."""
    teams = facts["teams"]
    bad = []
    by_y = defaultdict(list)
    for (t, y), r in rows.items():
        by_y[y].append(r)
    for y in SEASONS:
        rs = by_y[y]
        if len(rs) != TEAMS:
            bad.append(f"{y}: {len(rs)} teams")
        if sum(r["wins"] for r in rs) != SEASON_WINS:
            bad.append(f"{y}: {sum(r['wins'] for r in rs)} wins")
        if sum(r["points_for"] for r in rs) != sum(r["points_against"] for r in rs):
            bad.append(f"{y}: points for and against differ")
        if sum(r["playoff_wins"] for r in rs) != PLAYOFF_GAMES or sum(r["playoff_losses"] for r in rs) != PLAYOFF_GAMES:
            bad.append(f"{y}: playoff games don't add up to {PLAYOFF_GAMES}")
        for c in {t["conf"] for t in teams.values()}:
            cr = [r for r in rs if teams[r["team_id"]]["conf"] == c]
            seeded = {r["conference_seed"]: r for r in cr if r["conference_seed"]}
            if sorted(seeded) != list(range(1, SEEDS + 1)):
                bad.append(f"{y} {c}: seeds {sorted(seeded)}")
                continue
            for k in (1, 2, 3, 5, 6):
                if seeded[k]["wins"] < seeded[k + 1]["wins"]:
                    bad.append(f"{y} {c}: seed {k} has fewer wins than seed {k + 1}")
            out_best = max((r["wins"] for r in cr if not r["conference_seed"]), default=0)
            if seeded[7]["wins"] < out_best:
                bad.append(f"{y} {c}: a team that missed has more wins than the 7 seed")
            for k in range(1, 5):
                r = seeded[k]
                if r["division_place"] != 1:
                    bad.append(f"{y} {c}: seed {k} isn't a division winner")
            if not bracket_ok({k: r["playoff_result"] for k, r in seeded.items()}):
                bad.append(f"{y} {c}: the playoff results can't come from one bracket")
        win, lose, gid, g = d["sb"][y]
        if rows[(win, y)]["playoff_result"] != "CHAMPION" or rows[(lose, y)]["playoff_result"] != "SUPER_BOWL_LOSS":
            bad.append(f"{y}: the Super Bowl teams aren't {teams[win]['abbr']} over {teams[lose]['abbr']}")
    for (t, y), r in rows.items():
        if r["wins"] + r["losses"] != GAMES:
            bad.append(f"{teams[t]['abbr']} {y}: {r['wins']}-{r['losses']}")
        if r["playoff_result"] != "MISSED" and not r["conference_seed"]:
            bad.append(f"{teams[t]['abbr']} {y}: playoff result without a seed")
    for c, n in d["excess"].items():
        if sum(1 for i in offset if teams[d["groups"][i]["team"]]["conf"] == c) != n:
            bad.append(f"{c}: {n} playoff-loss offsets needed")
    for i, g in enumerate(d["groups"]):
        ys = [(g["team"], y) for y in g["seasons"]]
        g = dict(g, playoff_losses=g["playoff_losses"] - (i in offset)) if g["playoff_losses"] is not None else g
        got = {"wins": sum(rows[k]["wins"] for k in ys), "losses": sum(rows[k]["losses"] for k in ys),
               "winning_seasons": sum(1 for k in ys if rows[k]["wins"] > rows[k]["losses"]),
               "playoff_wins": sum(rows[k]["playoff_wins"] for k in ys),
               "playoff_losses": sum(rows[k]["playoff_losses"] for k in ys),
               "sb_wins": sum(1 for k in ys if rows[k]["playoff_result"] == "CHAMPION"),
               "sb_losses": sum(1 for k in ys if rows[k]["playoff_result"] == "SUPER_BOWL_LOSS")}
        for k, v in got.items():
            if g.get(k) is not None and v != g[k]:
                bad.append(f"coach {g['coach']} ({teams[g['team']]['abbr']}): {k} {v}, needs {g[k]}")
    for key, val in d["fixed"].items():
        if key in rows and rows[key]["wins"] != val:
            bad.append(f"{teams[key[0]]['abbr']} {key[1]}: {rows[key]['wins']} wins, fixed at {val}")
    for key, lo in d["lower"].items():
        if key in rows and not (lo <= rows[key]["wins"] <= d["upper"][key]):
            bad.append(f"{teams[key[0]]['abbr']} {key[1]}: {rows[key]['wins']} wins against its stored games")
    for key, (pf, pa) in d["points"].items():
        if key in rows and (rows[key]["points_for"], rows[key]["points_against"]) != (pf, pa):
            bad.append(f"{teams[key[0]]['abbr']} {key[1]}: points differ from its stored games")
    for g in d["playoff_games"]:
        rw, rl = rows[(g["winner"], g["season"])], rows[(g["loser"], g["season"])]
        if rl["playoff_result"] != g["round"] or rw["playoff_result"] in ("MISSED", g["round"]):
            bad.append(f"{g['season']} {g['round']}: {teams[g['winner']]['abbr']} over {teams[g['loser']]['abbr']} "
                       f"doesn't hold")
    cn = d["canon"]
    for key, hi in cn["max_wins"].items():
        if rows[key]["wins"] > hi:
            bad.append(f"{teams[key[0]]['abbr']} {key[1]}: more than {hi} wins")
    for key in cn["no_playoffs"]:
        if rows[key]["playoff_result"] != "MISSED":
            bad.append(f"{teams[key[0]]['abbr']} {key[1]}: made the playoffs")
    for key in cn["seed1"]:
        if rows[key]["conference_seed"] != 1:
            bad.append(f"{teams[key[0]]['abbr']} {key[1]}: not the 1 seed")
    for y, t in cn["worst_alone"].items():
        if any(r["wins"] <= rows[(t, y)]["wins"] for r in by_y[y] if r["team_id"] != t):
            bad.append(f"{y}: {teams[t]['abbr']} isn't alone at the bottom")
    for y, t in cn["worst_tied"].items():
        others = [r["wins"] for r in by_y[y] if r["team_id"] != t]
        if min(others) != rows[(t, y)]["wins"]:
            bad.append(f"{y}: {teams[t]['abbr']} isn't tied for the worst record")
    for y, t in cn["top_offense"].items():
        if any(r["points_for"] >= rows[(t, y)]["points_for"] for r in by_y[y] if r["team_id"] != t):
            bad.append(f"{y}: {teams[t]['abbr']} isn't the No. 1 offense")
    for pin in pins:
        if "team" not in pin:
            continue
        r = rows[(pin["team"], pin["season"])]
        if "wins" in pin and r["wins"] != pin["wins"]:
            bad.append(f"pin {pin}: {r['wins']} wins")
    return bad


def bracket_ok(result_by_seed):
    """True when some bracket outcome gives exactly these results by seed (pure)."""
    want = {k: v for k, v in result_by_seed.items()}
    for out in OUTCOMES:
        got = {k: out["lost"].get(k, "SB") for k in range(1, SEEDS + 1)}
        if all((got[k] == "SB") == (want[k] in ("CHAMPION", "SUPER_BOWL_LOSS")) and
               (got[k] == "SB" or got[k] == want[k]) for k in got):
            return True
    return False


# ------------------------------------------------------------------------------------------------- pins ----

def read_pins(by_abbr, path=PINS_JSON):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        raw = json.load(f)
    pins = [{**p, "team": by_abbr[p["team"]]} for p in raw.get("team_seasons", [])]
    if raw.get(OFFSET_KEY):
        pins.append({OFFSET_KEY: [by_abbr[t] for t in raw[OFFSET_KEY]]})
    return pins


# ------------------------------------------------------------------------------------------------ write ----

def generate(seeds=3, pins_path=PINS_JSON, time_limit=180, conn_info=CONN_INFO):
    """Solve `seeds` candidates, stage them, write the review report. Returns [(candidate id, status, problems)]."""
    with psycopg.connect(conn_info) as conn, conn.cursor() as cur:
        facts = load_facts(cur)
        d, fact_problems = derive(facts)
        pins = read_pins(facts["by_abbr"], pins_path)
        if fact_problems:
            raise SystemExit("facts don't fit together; nothing generated:\n  " + "\n  ".join(fact_problems))
        made = []
        for seed in range(1, seeds + 1):
            targets = default_targets(facts, d, seed)
            sol, status, objective = solve(facts, d, targets, pins, time_limit)
            if sol is None:
                fixes = diagnose(facts, d, targets, pins)
                raise SystemExit(f"seed {seed}: no history fits every fact ({status}); leaving out one family at a "
                                 f"time, these make it feasible: {fixes or 'none alone'}")
            rows = finish(facts, d, sol, seed)
            problems = verify(facts, d, rows, pins, sol["offset"])
            offset = [facts["teams"][d["groups"][i]["team"]]["abbr"] for i in sol["offset"]]
            cur.execute("""INSERT INTO history_candidates (seed, objective, solver_status, pins, report)
                           VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                        (seed, objective, status, json.dumps(pins),
                         json.dumps({"problems": problems, OFFSET_KEY: offset})))
            cid = cur.fetchone()[0]
            for r in rows.values():
                cur.execute("""INSERT INTO history_team_seasons (candidate_id, season, team_id, wins, losses, ties,
                                   points_for, points_against, division_place, division_tiebreak, conference_seed,
                                   playoff_result, playoff_wins, playoff_losses, head_coach_id, gm_id)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                            (cid, r["season"], r["team_id"], r["wins"], r["losses"], r["ties"], r["points_for"],
                             r["points_against"], r["division_place"], r["division_tiebreak"], r["conference_seed"],
                             r["playoff_result"], r["playoff_wins"], r["playoff_losses"], r["head_coach_id"],
                             r["gm_id"]))
            conn.commit()
            made.append((cid, status, problems, rows, offset))
        write_report(facts, d, made)
    return [(cid, status, problems) for cid, status, problems, *_ in made]


def apply(candidate, conn_info=CONN_INFO):
    """Copy one candidate into league_seasons, team_season_results and coach_season_lines (source SYNTH), replacing
    any SYNTH rows, in one transaction. Refuses if a 2026-2034 row that isn't SYNTH exists."""
    with psycopg.connect(conn_info) as conn, conn.cursor() as cur:
        cur.execute("SELECT report FROM history_candidates WHERE id = %s", (candidate,))
        got = cur.fetchone()
        if not got:
            raise SystemExit(f"no candidate {candidate}")
        if got[0] and got[0].get("problems"):
            raise SystemExit(f"candidate {candidate} fails verify(): {got[0]['problems'][:5]}")
        for table in ("league_seasons", "team_season_results", "coach_season_lines"):
            cur.execute(f"SELECT count(*) FROM {table} WHERE season BETWEEN %s AND %s AND source <> 'SYNTH'",
                        (FIRST, LAST))
            if cur.fetchone()[0]:
                raise SystemExit(f"{table} has non-SYNTH rows for {FIRST}-{LAST}; nothing applied")
        _delete_synth(cur)
        cur.execute("""SELECT season, team_id, wins, losses, ties, points_for, points_against, division_place,
                              division_tiebreak, conference_seed, playoff_result, playoff_wins, playoff_losses,
                              head_coach_id, gm_id
                       FROM history_team_seasons WHERE candidate_id = %s""", (candidate,))
        rows = cur.fetchall()
        sb = {}
        for r in rows:
            if r[10] in ("CHAMPION", "SUPER_BOWL_LOSS"):
                sb.setdefault(r[0], {})[r[10]] = r[1]
        for y in SEASONS:
            cur.execute("SELECT id FROM games WHERE season = %s AND game_type = 'SUPER_BOWL'", (y,))
            g = cur.fetchone()
            cur.execute("""INSERT INTO league_seasons (season, champion_team_id, runner_up_team_id, super_bowl_game_id,
                               regular_season_games, postseason_games, notes, source)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, 'SYNTH')""",
                        (y, sb[y]["CHAMPION"], sb[y]["SUPER_BOWL_LOSS"], g[0] if g else None, SEASON_WINS,
                         PLAYOFF_GAMES, f"generated history, candidate {candidate}"))
        for r in rows:
            cur.execute("""INSERT INTO team_season_results (season, team_id, wins, losses, ties, points_for,
                               points_against, division_place, division_tiebreak, conference_seed, playoff_result,
                               playoff_wins, playoff_losses, head_coach_id, gm_id, source)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'SYNTH')""", r)
            if r[13] is not None:
                cur.execute("""INSERT INTO coach_season_lines (coach_id, team_id, season, wins, losses, ties, games,
                                   playoff_wins, playoff_losses, playoff_result, source)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'SYNTH')""",
                            (r[13], r[1], r[0], r[2], r[3], r[4], r[2] + r[3] + r[4], r[11], r[12], r[10]))
        cur.execute("UPDATE history_candidates SET applied_at = CASE WHEN id = %s THEN now() END", (candidate,))
        conn.commit()
    return len(rows)


def revert(conn_info=CONN_INFO):
    with psycopg.connect(conn_info) as conn, conn.cursor() as cur:
        n = _delete_synth(cur)
        cur.execute("UPDATE history_candidates SET applied_at = NULL")
        conn.commit()
    return n


def _delete_synth(cur):
    n = 0
    for table in ("coach_season_lines", "team_season_results", "league_seasons"):
        cur.execute(f"DELETE FROM {table} WHERE source = 'SYNTH'")
        n += cur.rowcount
    return n


# ----------------------------------------------------------------------------------------------- report ----

def write_report(facts, d, made):
    teams = facts["teams"]
    ab = lambda t: teams[t]["abbr"]
    lines = ["# 2026–2034 team arc candidates", "",
             "Each candidate meets every fact (coach totals, stored games, Super Bowls, canon); they differ in the "
             "path. Pick one with `python3 -m engines.history_backfill apply --candidate N`, or pin seasons in "
             "`data/history/pins.json` and generate again.", "",
             "Adjustments: Todd Bowles (TB) " + ", ".join(f"{k} {v:+d}" for k, v in BOWLES[38].items()) +
             " against his stored 2025 row; Carolina 2026–2033 (Canales) takes the league's remainder; one AFC "
             "coach's playoff trips are one fewer than his snapshot difference (the AFC's differences add up to 64 "
             "trips, the league has 63), chosen by the solver among coaches with a real pre-2026 record.", ""]
    for cid, status, problems, rows, offset in made:
        lines += [f"## Candidate {cid} ({status}; verify: {'clean' if not problems else '; '.join(problems[:5])})", "",
                  f"The extra AFC playoff loss is on: {', '.join(offset) or 'nobody'}.", ""]
        lines.append("| Team | " + " | ".join(str(y) for y in SEASONS) + " | Total |")
        lines.append("|---|" + "---|" * (len(SEASONS) + 1))
        for t in sorted(teams, key=lambda t: (teams[t]["conf"], teams[t]["div"], ab(t))):
            cells = []
            for y in SEASONS:
                r = rows[(t, y)]
                mark = {"CHAMPION": "🏆", "SUPER_BOWL_LOSS": " SB", "CONFERENCE": " CC", "DIVISIONAL": " D",
                        "WILD_CARD": " WC"}.get(r["playoff_result"], "")
                cells.append(f"{r['wins']}-{r['losses']}{mark}")
            tw = sum(rows[(t, y)]["wins"] for y in SEASONS)
            lines.append(f"| {ab(t)} | " + " | ".join(cells) + f" | {tw}-{GAMES * len(SEASONS) - tw} |")
        lines += ["", "Spread by season (2035: 5 teams at 12+ wins, 5 at 5 or fewer, 12 at 8-9):", ""]
        for y in SEASONS:
            ws_ = [rows[(t, y)]["wins"] for t in teams]
            lines.append(f"- {y}: {sum(w >= 12 for w in ws_)} at 12+, {sum(w <= 5 for w in ws_)} at 5 or fewer, "
                         f"{sum(w in (8, 9) for w in ws_)} at 8-9")
        lines += ["", "Seeds by season (AFC / NFC, 1–7):", ""]
        for y in SEASONS:
            parts = []
            for c in ("AFC", "NFC"):
                s = sorted((r["conference_seed"], ab(t)) for (t, yy), r in rows.items()
                           if yy == y and r["conference_seed"] and teams[t]["conf"] == c)
                parts.append(" ".join(a for _, a in s))
            lines.append(f"- {y}: {parts[0]} / {parts[1]}")
        lines.append("")
    os.makedirs(os.path.dirname(REPORT_MD), exist_ok=True)
    with open(REPORT_MD, "w") as f:
        f.write("\n".join(lines) + "\n")
