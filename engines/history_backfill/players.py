"""
Phase 3 run: cast -> team totals -> player lines -> fill-ins -> validate -> stage (docs/HISTORY_BACKFILL_PLAN.md
phase 3; PROJECT_SPEC §3.3k).

  python3 -m engines.history_backfill players [--seed 1]

Writes one history_player_runs row with its history_players and history_player_lines, and
verify_output/history_player_lines.md (counts, each season's leaders, fill-ins by team, problems). Nothing reaches
the live player tables; that is a later apply step after JR's review. validate() is pure.
"""
import json
import os
import random
from collections import Counter, defaultdict

import psycopg

from engines.history_backfill import cast as cast_mod
from engines.history_backfill import fillins as fill_mod
from engines.history_backfill import player_lines as pl
from engines.history_backfill import team_totals as tt

CONN_INFO = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")
ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
REPORT_MD = os.path.join(ROOT, "verify_output", "history_player_lines.md")
NAMES_CSV = os.path.join(ROOT, "data", "history", ".cache", "nflverse_players.csv")
LEADERS = (("pass_yards", "QB"), ("rush_yards", None), ("rec_yards", None), ("def_sacks", None), ("def_ints", None))


def validate(members, lines, inp):
    """Every constraint re-checked on the lines (pure): constrained players' sums equal their remainders, pins hold,
    no unpinned season passes its record ceiling, award seasons qualify. Returns [problem]."""
    bad = []
    for m in members:
        if not m.get("constrained"):
            continue
        rem = inp["remainders"][m["player_id"]]
        for stat in pl.LINE_STATS:
            want = max(0, int(round(rem.get(stat) or 0)))
            got = sum(lines.get((m["key"], y), {}).get(stat, 0) - inp["canon"].get((m["player_id"], y), {}).get(stat, 0)
                      for y in m["seasons"])          # a canon row counts only what was added to it
            if got != want and not (stat in ("rush_yac", "rec_rac") and got < want):
                bad.append(f"{m['full_name']}: {stat} {got}, remainder {want}")
    for (key, y), pin in inp["pins"].items():
        for stat, v in pin.items():
            got = lines.get((key, y), {}).get(stat, 0)
            if got != v:
                bad.append(f"{key} {y}: {stat} {got}, the record book says {v}")
    for m in members:
        for y in m["seasons"]:
            row = lines.get((m["key"], y), {})
            for stat, v in row.items():
                if stat in inp["pins"].get((m["key"], y), {}):
                    continue
                cap = inp["ceilings"](m, y, stat) if stat in pl.LINE_STATS else None
                if cap is not None and v > cap:
                    bad.append(f"{m['full_name']} {y}: {stat} {v} above its record ceiling {cap}")
    by_key = {m["key"]: m for m in members}
    for y, award, key in inp["awards"]:
        m = by_key[key]
        if award in ("OROY", "DROY") and m["exp"].get(y) != 0:
            bad.append(f"{y} {award}: {m['full_name']} isn't a rookie")
    return bad


# a named fill-in's season from the game record he holds: the record game plus a backup's other games
NAMED_LINE = {"receptions": {"receptions": 3, "rec_yards": 32, "rec_tds": 0.2},
              "sacks": {"def_sacks": 1.6, "def_solo": 9, "def_assists": 4, "def_tfl": 2}}


def named_line(fact):
    """A fill-in record holder's season (pure): his record game's value plus about a third of a season more."""
    cat = fact["kind"].split(":", 1)[1]
    value = int(fact.get("value") or 0)
    for word, extra in NAMED_LINE.items():
        if word in cat:
            row = {s: int(round(v * 6)) for s, v in extra.items()}
            key = "receptions" if word == "receptions" else "def_sacks"
            row[key] += value
            return row
    return {}


def run(seed=1, conn_info=CONN_INFO):
    with psycopg.connect(conn_info) as conn, conn.cursor() as cur:
        co = cast_mod.load(cur, seed)
        totals = tt.draw(tt.fit(tt.calibrate(cur)), tt.arc(cur), seed)
        inp = pl.inputs(cur, co)
        cur.execute("SELECT full_name FROM players WHERE full_name IS NOT NULL")
        taken = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT id, abbreviation FROM teams")
        abbr = {i: a.strip() for i, a in cur.fetchall()}
        cur.execute("SELECT id FROM history_candidates WHERE applied_at IS NOT NULL")
        arc_row = cur.fetchone()
        members = co["members"]
        lines, leftover, problems = pl.generate(members, totals, inp["remainders"], inp["canon"], inp["pins"],
                                                inp["floors"], inp["ceilings"], inp["awards"], seed)
        known, seen = [], set()
        for f in co["unknown"]:                          # record holders nobody in the cast can be: named fill-ins
            if f["name"] not in seen:
                seen.add(f["name"])
                known.append({"name": f["name"], "team": f["team"], "season": f["season"],
                              "position": f.get("position", "WR")})
        fills, unassigned = fill_mod.build(leftover, fill_mod.real_names(NAMES_CSV), taken, known, seed)
        rng = random.Random(seed + 11)
        named = {k["name"] for k in known}
        floors = {(f["name"], f["season"]): f for f in co["unknown"]}
        for f in fills:
            if f["full_name"] in named:                  # a record holder: a backup-sized season that holds his game
                y = min(f["seasons"])
                fact = next(v for (n, yy), v in floors.items() if n == f["full_name"])
                f["lines"][y] = named_line(fact)
            for y, row in f["lines"].items():
                row["games_played"] = rng.randint(12, 17)
                pl.consistent(row)
                lines[(f["key"], y)] = row
        problems += validate(members, lines, inp)
        everyone = {m["key"]: m for m in members + fills}
        keys = {k for k, _ in lines}

        report = {"problems": problems, "players": len(keys), "lines": len(lines), "fillins": len(fills),
                  "kinds": dict(Counter(everyone[k]["kind"] for k in keys)), "unresolved": inp["unresolved"]}
        cur.execute("INSERT INTO history_player_runs (seed, team_arc_candidate, report) VALUES (%s, %s, %s) "
                    "RETURNING id", (seed, arc_row[0] if arc_row else None, json.dumps(report, default=str)))
        run_id = cur.fetchone()[0]
        for k in sorted(keys):
            m = everyone[k]
            cur.execute("""INSERT INTO history_players (run_id, key, kind, player_id, espn_id, full_name, player_name,
                               position, first_season) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (run_id, k, m["kind"], m.get("player_id"), m.get("espn_id"), m["full_name"] or m["name"],
                         m["name"], m["position"], m.get("first")))
        for (k, y), row in sorted(lines.items()):
            m = everyone[k]
            cur.execute("""INSERT INTO history_player_lines (run_id, key, season, team_id, position, age,
                               years_of_experience, stats) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                        (run_id, k, y, m["seasons"][y], m["position"], m["ages"].get(y), m["exp"].get(y),
                         json.dumps({s: v for s, v in row.items() if v})))
        conn.commit()
    write_report(run_id, everyone, lines, fills, unassigned, report, abbr)
    return run_id, report


def write_report(run_id, everyone, lines, fills, unassigned, report, abbr):
    mark = {"pool": "", "vet": " (r)", "fillin": " (f)"}
    out = [f"# 2026–2034 player lines, run {run_id}", "",
           "Staged only: nothing is in the live player tables yet. (r) = a real 2026 player who retired before 2035; "
           "(f) = a fill-in; no mark = a current player whose lines add up to his Madden career totals.", "",
           f"- Players with lines: {report['players']} ({', '.join(f'{k} {v}' for k, v in sorted(report['kinds'].items()))}); "
           f"lines: {report['lines']}",
           f"- Problems: {len(report['problems'])}", ""]
    out += [f"  - {p}" for p in report["problems"][:40]]
    out += ["", "## Leaders by season", ""]
    for y in cast_mod.SEASONS:
        parts = []
        for stat, _ in LEADERS:
            best = sorted(((v.get(stat, 0), k) for (k, yy), v in lines.items() if yy == y), reverse=True)[:3]
            parts.append(f"{stat}: " + ", ".join(
                f"{everyone[k]['full_name']}{mark[everyone[k]['kind']]} {abbr[everyone[k]['seasons'][y]]} {v:g}"
                for v, k in best))
        out.append(f"- **{y}** " + " · ".join(parts))
    out += ["", "## Fill-ins", ""]
    by_team = defaultdict(list)
    for f in fills:
        by_team[abbr[f["team"]]].append(f)
    for t in sorted(by_team):
        out.append(f"- **{t}**: " + "; ".join(
            f"{f['full_name']} {f['position']} {min(f['seasons'])}–{max(f['seasons'])}" for f in by_team[t]))
    os.makedirs(os.path.dirname(REPORT_MD), exist_ok=True)
    with open(REPORT_MD, "w") as fh:
        fh.write("\n".join(out) + "\n")
