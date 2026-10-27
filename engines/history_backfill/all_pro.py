"""
All-Pro teams for 2026-2034 (docs/HISTORY_BACKFILL_PLAN.md §5, records plan D5; PROJECT_SPEC §3.3l).

  python3 -m engines.history_backfill all-pro [--dry-run]

First and Second Team for each history season in the stored 2035-2036 shape (newsroom/all_pro.py's 31 slots: QB, RB,
FB, 3 WR, TE, AP, LT, LG, C, RG, RT; 2 EDGE, 2 DI, 3 LB, 2 CB, SCB, 2 S; K, P, KR, PR, LS, ST), picked from the
season's lines (season_stat_lines) by a score per slot (SCORES), with the team's wins as a small tiebreaker. Award
winners come first: an MVP, OPOY or DPOY is First Team at his slot. AP is the best all-purpose yardage (rushing,
receiving and returns) among backs, receivers and tight ends not already picked; SCB is the next corner after the
two picked. Positions with no stats: LS is a long snapper active that season (a 2035-2036 one by years of
experience, else a real 2026 roster one, stored by name and team without a player link), ST a backup linebacker,
safety or corner (the most tackles among players who aren't one of their team's top two at the position). Stored
as awards rows with source = 'SYNTH'; every run replaces them. pick() is pure.
"""
import argparse
import csv
import json
import os
import random
from collections import defaultdict

import psycopg

CONN_INFO = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")
ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
REPORT_MD = os.path.join(ROOT, "verify_output", "history_all_pro.md")
FIRST, LAST = 2026, 2034
TEAMS = ("First Team All-Pro", "Second Team All-Pro")

# slot -> (eligible Madden positions, how many per team level)
SLOTS = (("QB", ("QB",), 1), ("RB", ("HB",), 1), ("FB", ("FB",), 1), ("WR", ("WR",), 3), ("TE", ("TE",), 1),
         ("AP", ("HB", "WR", "TE", "FB"), 1), ("LT", ("LT",), 1), ("LG", ("LG",), 1), ("C", ("C",), 1),
         ("RG", ("RG",), 1), ("RT", ("RT",), 1), ("EDGE", ("LEDG", "REDG"), 2), ("DI", ("DT",), 2),
         ("LB", ("MIKE", "WILL", "SAM"), 3), ("CB", ("CB",), 2), ("SCB", ("CB",), 1), ("S", ("FS", "SS"), 2),
         ("K", ("K",), 1), ("P", ("P",), 1), ("KR", None, 1), ("PR", None, 1), ("LS", None, 1), ("ST", None, 1))
SLOT_OF_POSITION = {"QB": "QB", "HB": "RB", "FB": "FB", "WR": "WR", "TE": "TE", "LT": "LT", "LG": "LG", "C": "C",
                    "RG": "RG", "RT": "RT", "LEDG": "EDGE", "REDG": "EDGE", "DT": "DI", "MIKE": "LB", "WILL": "LB",
                    "SAM": "LB", "CB": "CB", "FS": "S", "SS": "S", "K": "K", "P": "P"}


def g(r, k):
    return float(r.get(k) or 0)


SCORES = {
    "QB": lambda r: g(r, "pass_yards") + 20 * g(r, "pass_tds") - 45 * g(r, "interceptions") + g(r, "rush_yards")
    + 20 * g(r, "rush_tds"),
    "RB": lambda r: g(r, "rush_yards") + g(r, "rec_yards") + 60 * (g(r, "rush_tds") + g(r, "rec_tds")),
    "FB": lambda r: g(r, "rush_yards") + g(r, "rec_yards") + 60 * (g(r, "rush_tds") + g(r, "rec_tds")) + 5 * g(r, "games_played"),
    "WR": lambda r: g(r, "rec_yards") + 60 * g(r, "rec_tds") + 2 * g(r, "receptions"),
    "TE": lambda r: g(r, "rec_yards") + 60 * g(r, "rec_tds") + 2 * g(r, "receptions"),
    "AP": lambda r: g(r, "rush_yards") + g(r, "rec_yards") + g(r, "kr_yards") + g(r, "pr_yards")
    + 50 * (g(r, "rush_tds") + g(r, "rec_tds")),
    "OL": lambda r: 12 * g(r, "games_played") - 25 * g(r, "blk_sacks_allowed"),
    "EDGE": lambda r: 100 * g(r, "def_sacks") + 20 * g(r, "def_tfl") + 3 * g(r, "def_tackles") + 30 * g(r, "def_forced_fumbles"),
    "DI": lambda r: 100 * g(r, "def_sacks") + 25 * g(r, "def_tfl") + 4 * g(r, "def_tackles") + 30 * g(r, "def_forced_fumbles"),
    "LB": lambda r: 3 * g(r, "def_tackles") + 60 * g(r, "def_sacks") + 80 * g(r, "def_ints") + 20 * g(r, "def_tfl")
    + 15 * g(r, "def_deflections") + 30 * g(r, "def_forced_fumbles"),
    "CB": lambda r: 90 * g(r, "def_ints") + 25 * g(r, "def_deflections") + 2 * g(r, "def_tackles") + 60 * g(r, "def_tds"),
    "S": lambda r: 3 * g(r, "def_tackles") + 90 * g(r, "def_ints") + 25 * g(r, "def_deflections") + 40 * g(r, "def_sacks"),
    "K": lambda r: 6 * g(r, "kick_fgm") + 120 * (g(r, "kick_fgm") / g(r, "kick_fga") if g(r, "kick_fga") else 0),
    "P": lambda r: (g(r, "punt_net_average") or g(r, "punt_average")) * (1 if g(r, "punt_punts") >= 40 else 0.5),
    "KR": lambda r: g(r, "kr_yards"), "PR": lambda r: g(r, "pr_yards"),
}
for s in ("LT", "LG", "C", "RG", "RT"):
    SCORES[s] = SCORES["OL"]
SCORES["SCB"] = SCORES["CB"]
AWARD_SLOTS = ("MVP", "OPOY", "DPOY")


def pick(rows, wins, awards, ls_pool, seed=1):
    """rows: one season's lines [{player_id, name, team_id, position, stats...}]; wins {team_id: wins}; awards
    [(award, player_id)]; ls_pool [(player_id or None, name, team_id)]. Returns {team level: [(slot, row)]} (pure)."""
    rng = random.Random(seed)
    tb = lambda r: wins.get(r["team_id"], 8) * 0.5                 # a small tiebreaker for winning teams
    taken = set()
    out = {t: [] for t in TEAMS}
    forced = defaultdict(list)                                     # slot -> award winners' rows (First Team)
    by_pid = {r["player_id"]: r for r in rows if r.get("player_id")}
    for award, pid in awards:
        r = by_pid.get(pid)
        if award in AWARD_SLOTS and r and SLOT_OF_POSITION.get(r["position"]):
            forced[SLOT_OF_POSITION[r["position"]]].append(r)
    for slot, positions, n in SLOTS:
        if slot in ("LS", "ST", "SCB"):
            continue                                               # SCB comes with CB; LS and ST below
        if slot in ("KR", "PR"):
            elig = [r for r in rows if g(r, f"{slot.lower()}_yards") > 0]
        else:
            elig = [r for r in rows if r["position"] in positions]
        ranked = sorted(elig, key=lambda r: -(SCORES[slot](r) + tb(r)))
        returner = slot in ("KR", "PR")                            # a returner can also hold a roster slot
        in_slot = set()
        for level_i, level in enumerate(TEAMS):
            chosen = [r for r in (forced.get(slot, []) if level_i == 0 else [])
                      if r["player_id"] not in taken][:n]
            for r in ranked:
                if len(chosen) >= n:
                    break
                if r in chosen or r["player_id"] in in_slot or (not returner and r["player_id"] in taken):
                    continue
                chosen.append(r)
            for r in chosen:
                in_slot.add(r["player_id"])
                if not returner:
                    taken.add(r["player_id"])
            out[level] += [(slot, r) for r in chosen]
            if slot == "CB":                                       # the slot corner: the next corner after them
                nxt = next((r for r in ranked if r["player_id"] not in taken), None)
                if nxt:
                    out[level].append(("SCB", nxt))
                    taken.add(nxt["player_id"])
    # ST: a backup LB / S / CB with the most tackles (not one of his team's top two at the position)
    top2 = defaultdict(list)
    for r in sorted(rows, key=lambda r: -g(r, "def_tackles")):
        top2[(r["team_id"], r["position"])].append(r["player_id"])
    backups = [r for r in rows if r["position"] in ("MIKE", "WILL", "SAM", "FS", "SS", "CB")
               and r["player_id"] not in top2[(r["team_id"], r["position"])][:2] and r["player_id"] not in taken]
    backups.sort(key=lambda r: -(g(r, "def_tackles") + g(r, "games_played") + tb(r)))
    lsp = sorted(ls_pool, key=lambda x: -wins.get(x[2], 8) + rng.random())
    for i, level in enumerate(TEAMS):
        if len(backups) > i:
            out[level].append(("ST", backups[i]))
        if len(lsp) > i:
            pid, name, team = lsp[i]
            out[level].append(("LS", {"player_id": pid, "name": name, "team_id": team, "position": "LS"}))
    return out


# ------------------------------------------------------------------------------------------------- run ----

def load_season(cur, y):
    cur.execute("""SELECT l.*, COALESCE(p.full_name, p.player_name) AS name FROM season_stat_lines l
                   JOIN players p ON p.id = l.player_id WHERE l.season = %s""", (y,))
    cols = [d.name for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    cur.execute("SELECT team_id, wins FROM team_season_results WHERE season = %s", (y,))
    wins = dict(cur.fetchall())
    cur.execute("SELECT award_type, player_id FROM awards WHERE season = %s AND player_id IS NOT NULL", (y,))
    awards = cur.fetchall()
    # long snappers active that season: a 2035-2036 one by years of experience (his 2035 team), else real 2026 ones
    cur.execute("""SELECT DISTINCT ON (p.id) p.id, COALESCE(p.full_name, p.player_name), s.team_id
                   FROM players p JOIN player_seasons s ON s.player_id = p.id
                   WHERE s.position = 'LS' AND s.season IN (2035, 2036) AND s.season - s.years_of_experience <= %s
                   ORDER BY p.id, s.season""", (y,))
    ls = cur.fetchall()
    if len(ls) < 2:
        cur.execute("SELECT id, abbreviation FROM teams")
        by_abbr = {a.strip(): i for i, a in cur.fetchall()}
        with open(os.path.join(ROOT, "data", "history", "rosters_2026.csv")) as f:
            ls += [(None, r["full_name"], by_abbr.get(r["team"])) for r in csv.DictReader(f) if r["position"] == "LS"]
    return rows, wins, awards, ls


def run(dry_run=False, conn_info=CONN_INFO, seed=1):
    """Pick and store every history season's All-Pro teams (replacing SYNTH ones); write the report."""
    with psycopg.connect(conn_info) as conn, conn.cursor() as cur:
        teams = picks_for(cur, seed)
        store(cur, teams)
        if dry_run:
            conn.rollback()
        else:
            conn.commit()
        cur.execute("SELECT id, abbreviation FROM teams")
        abbr = {i: a.strip() for i, a in cur.fetchall()}
    write_report(teams, abbr)
    return {y: sum(len(v) for v in t.values()) for y, t in teams.items()}


def picks_for(cur, seed=1):
    return {y: pick(*load_season(cur, y), seed=seed + y) for y in range(FIRST, LAST + 1)}


def store(cur, teams):
    cur.execute("DELETE FROM awards WHERE source = 'SYNTH' AND award_type = ANY(%s)", (list(TEAMS),))
    for y, levels in teams.items():
        for level, picks in levels.items():
            for slot, r in picks:
                cur.execute("""INSERT INTO awards (season, award_type, player_id, team_id, player_name, position, source)
                               VALUES (%s, %s, %s, %s, %s, %s, 'SYNTH')""",
                            (y, level, r.get("player_id"), r.get("team_id"), r["name"], slot))


def write_report(teams, abbr):
    out = ["# All-Pro teams, 2026–2034 history", "",
           "Picked from the reconstructed season lines (engines/history_backfill/all_pro.py); MVP, OPOY and DPOY "
           "winners are First Team at their slot.", ""]
    for y, levels in teams.items():
        out.append(f"## {y}")
        for level, picks in levels.items():
            out.append(f"- **{level}:** " + "; ".join(f"{s} {r['name']} ({abbr.get(r.get('team_id'), '?')})"
                                                       for s, r in picks))
        out.append("")
    os.makedirs(os.path.dirname(REPORT_MD), exist_ok=True)
    with open(REPORT_MD, "w") as fh:
        fh.write("\n".join(out) + "\n")
