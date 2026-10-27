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
                                                inp["floors"], inp["ceilings"], inp["awards"], seed, inp["soft"])
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
        # fill-ins come after the record caps in generate(): cap them here (a fill-in holds no record)
        for f in fills:
            f.setdefault("group", cast_mod.GROUP.get(f["position"]))
            f["constrained"] = False
            for y in f["seasons"]:
                row = lines.get((f["key"], y), {})
                for stat in list(row):
                    cap = inp["ceilings"](f, y, stat) if stat in pl.LINE_STATS else None
                    soft = inp["soft"].get(stat)
                    cap = min(c for c in (cap, soft) if c is not None) if (cap is not None or soft) else None
                    if cap is not None and row[stat] > cap:
                        row[stat] = pl.below_cap(int(cap), (f["key"], y), stat)
            pl._scrimmage(lines, f, inp["ceilings"], {})
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


# ---------------------------------------------------------------------------------------------- apply ----

SS_COLS = ("completions", "pass_attempts", "pass_yards", "pass_tds", "interceptions", "pass_sacks", "rush_attempts",
           "rush_yards", "rush_tds", "fumb", "rush_btk", "rush_yac", "receptions", "rec_yards", "rec_tds", "rec_rac")
# season_stat_lines name -> (table, column)
SIDE = {**{f"def_{c}": ("season_stat_defense", c) for c in (
            "solo", "assists", "tackles", "tfl", "sacks", "ints", "int_yds", "deflections", "forced_fumbles",
            "fumble_recoveries", "tds", "safeties")},
        "kick_fgm": ("season_stat_kicking", "fgm"), "kick_fga": ("season_stat_kicking", "fga"),
        "kick_xpm": ("season_stat_kicking", "xpm"), "kick_xpa": ("season_stat_kicking", "xpa"),
        "punt_punts": ("season_stat_punting", "punts"), "punt_yards": ("season_stat_punting", "yards"),
        "punt_net_yards": ("season_stat_punting", "net_yards"), "punt_inside_20": ("season_stat_punting", "inside_20"),
        "kr_returns": ("season_stat_kickreturn", "returns"), "kr_yards": ("season_stat_kickreturn", "yards"),
        "pr_returns": ("season_stat_puntreturn", "returns"), "pr_yards": ("season_stat_puntreturn", "yards"),
        "blk_sacks_allowed": ("season_stat_blocking", "sacks_allowed"),
        "games_played": ("season_stat_general", "games_played")}
PLAYER_TABLES = ("season_stat_general", "season_stat_blocking", "season_stat_puntreturn", "season_stat_kickreturn",
                 "season_stat_punting", "season_stat_kicking", "season_stat_defense", "season_stats", "player_seasons")
ORIGIN = {"vet": "HISTORY", "fillin": "HISTORY_FILLIN"}


def derived(table, row):
    """Rates the side tables and season_stats carry, from the counting stats (pure)."""
    def rate(a, b, nd=1):
        return round(a / b, nd) if b else None
    g = row.get("games_played") or 0
    if table == "season_stats":
        return {"pass_comp_pct": rate(100 * row.get("completions", 0), row.get("pass_attempts", 0)),
                "pass_ypa": rate(row.get("pass_yards", 0), row.get("pass_attempts", 0)),
                "pass_ypg": rate(row.get("pass_yards", 0), g), "rush_avg": rate(row.get("rush_yards", 0),
                                                                                row.get("rush_attempts", 0)),
                "rush_ypg": rate(row.get("rush_yards", 0), g), "rec_avg": rate(row.get("rec_yards", 0),
                                                                              row.get("receptions", 0)),
                "rec_ypg": rate(row.get("rec_yards", 0), g)}
    if table == "season_stat_defense":
        return {"int_avg": rate(row.get("int_yds", 0), row.get("ints", 0))}
    if table == "season_stat_kicking":
        return {"fg_pct": rate(100 * row.get("fgm", 0), row.get("fga", 0)),
                "xp_pct": rate(100 * row.get("xpm", 0), row.get("xpa", 0))}
    if table == "season_stat_punting":
        return {"average": rate(row.get("yards", 0), row.get("punts", 0)),
                "net_average": rate(row.get("net_yards", 0), row.get("punts", 0))}
    if table in ("season_stat_kickreturn", "season_stat_puntreturn"):
        return {"average": rate(row.get("yards", 0), row.get("returns", 0))}
    return {}


def apply_run(run_id, conn_info=CONN_INFO, dry_run=False):
    """Write a staged run into players / player_seasons / season_stats / season_stat_* (source SYNTH) in one
    transaction. Canon season_stats rows (the seeded Panthers lines) are updated in place: only their unstated
    columns and the listed canon adjustments, with the old values kept in the run's report for revert. Refuses a run
    with problems other than zero-counted negatives and canon adjustments, or when SYNTH player rows exist."""
    with psycopg.connect(conn_info) as conn, conn.cursor() as cur:
        cur.execute("SELECT report FROM history_player_runs WHERE id = %s", (run_id,))
        got = cur.fetchone()
        if not got:
            raise SystemExit(f"no run {run_id}")
        report = got[0]
        blocking = [p for p in report["problems"] if "counted as 0" not in p and not p.startswith(("canon adjustment", "note:"))]
        if blocking:
            raise SystemExit(f"run {run_id} has problems: {blocking[:5]}")
        cur.execute("SELECT count(*) FROM player_seasons WHERE source = 'SYNTH'")
        if cur.fetchone()[0]:
            raise SystemExit("SYNTH player rows exist: revert-players first")
        cur.execute("SELECT key, kind, player_id, espn_id, full_name, player_name FROM history_players WHERE run_id = %s",
                    (run_id,))
        pid = {}
        for key, kind, player_id, espn, full, name in cur.fetchall():
            if player_id:
                pid[key] = player_id
                continue
            cur.execute("INSERT INTO players (player_name, full_name, espn_id, origin) VALUES (%s, %s, %s, %s) "
                        "RETURNING id", (name, full, espn, ORIGIN[kind]))
            pid[key] = cur.fetchone()[0]
        cur.execute("""SELECT key, season, team_id, position, age, years_of_experience, stats FROM history_player_lines
                       WHERE run_id = %s ORDER BY season, key""", (run_id,))
        lines = cur.fetchall()
        cols = ("completions", "pass_attempts", "pass_yards", "pass_tds", "interceptions", "rush_attempts",
                "rush_yards", "rush_tds", "receptions", "rec_yards", "rec_tds", "fumb", "pass_sacks")
        cur.execute(f"SELECT id, player_id, season, {', '.join(cols)} FROM season_stats WHERE season BETWEEN 2026 AND 2034")
        canon = {(r[1], r[2]): (r[0], dict(zip(cols, r[3:]))) for r in cur.fetchall()}
        canon_changes, names_used, counts = [], set(), Counter()
        cur.execute("SELECT season, player_name, team_id, position FROM season_stats WHERE season BETWEEN 2026 AND 2034")
        names_used = {tuple(r) for r in cur.fetchall()}
        cur.execute("SELECT key, player_name, full_name FROM history_players WHERE run_id = %s", (run_id,))
        names = {k: (n, f) for k, n, f in cur.fetchall()}
        for key, y, team, pos, age, exp, stats in lines:
            p = pid[key]
            cur.execute("""INSERT INTO player_seasons (player_id, team_id, season, position, age, years_of_experience,
                               source) VALUES (%s, %s, %s, %s, %s, %s, 'SYNTH')""", (p, team, y, pos, age, exp))
            counts["player_seasons"] += 1
            off = {c: stats[c] for c in SS_COLS if stats.get(c)}
            if (p, y) in canon:
                row_id, old = canon[(p, y)]
                upd = {c: v for c, v in off.items() if c in old and (not old[c] or v != old[c])}
                upd.update({c: v for c, v in off.items() if c not in old})
                if upd:
                    canon_changes.append({"id": row_id, "old": {c: old.get(c) for c in upd}})
                    cur.execute(f"UPDATE season_stats SET {', '.join(f'{c} = %s' for c in upd)} WHERE id = %s",
                                (*upd.values(), row_id))
                    counts["canon_updates"] += 1
            elif off:
                name = names[key][0]
                if (y, name, team, pos) in names_used:
                    name = names[key][1]                     # two teammates share a list name: the full name
                names_used.add((y, name, team, pos))
                row = {**off, **{k: v for k, v in derived("season_stats", {**off, **stats}).items() if v is not None}}
                cur.execute(f"""INSERT INTO season_stats (season, player_id, player_name, team_id, position,
                                    {', '.join(row)}, source)
                                VALUES (%s, %s, %s, %s, %s, {', '.join(['%s'] * len(row))}, 'SYNTH')""",
                            (y, p, name, team, pos, *row.values()))
                counts["season_stats"] += 1
            side = defaultdict(dict)
            for stat, v in stats.items():
                if stat in SIDE and v:
                    t, c = SIDE[stat]
                    side[t][c] = v
            if "season_stat_defense" in side:
                d = side["season_stat_defense"]
                d["tackles"] = d.get("solo", 0) + d.get("assists", 0)
            for t, row in side.items():
                row = {**row, **{k: v for k, v in derived(t, row).items() if v is not None}}
                cur.execute(f"""INSERT INTO {t} (season, player_id, team_id, position, {', '.join(row)}, source)
                                VALUES (%s, %s, %s, %s, {', '.join(['%s'] * len(row))}, 'SYNTH')""",
                            (y, p, team, pos, *row.values()))
                counts[t] += 1
        report["applied"] = {"counts": dict(counts), "canon_changes": canon_changes, "linked_facts": link_facts(cur)}
        from engines.history_backfill import all_pro              # the history players' ids are new: re-pick All-Pro
        all_pro.store(cur, all_pro.picks_for(cur))
        cur.execute("UPDATE history_player_runs SET applied_at = now(), report = %s WHERE id = %s",
                    (json.dumps(report, default=str), run_id))
        if dry_run:
            conn.rollback()
        else:
            conn.commit()
    return dict(counts)


FACT_TABLES = (("awards", "player_name"), ("league_record_entries", "holder"), ("franchise_records", "holder"))


def link_facts(cur, first=2026, last=2034):
    """Point every unlinked 2026-2034 award and record-book row at its player: the one with that full name who has
    a player_seasons row that season (on the row's team when it names one). Returns [(table, row id, player id)]."""
    cur.execute("""SELECT ps.season, ps.team_id, p.id, COALESCE(p.full_name, p.player_name)
                   FROM player_seasons ps JOIN players p ON p.id = ps.player_id
                   WHERE ps.season BETWEEN %s AND %s""", (first, last))
    by = defaultdict(set)
    for y, team, pid, name in cur.fetchall():
        k = cast_mod.name_key(name)
        by[(k, y, None)].add(pid)
        by[(k, y, team)].add(pid)
    linked = []
    for table, col in FACT_TABLES:
        cur.execute(f"SELECT id, season, team_id, {col} FROM {table} WHERE player_id IS NULL AND {col} IS NOT NULL "
                    f"AND season BETWEEN %s AND %s", (first, last))
        for row_id, y, team, name in cur.fetchall():
            cands = by.get((cast_mod.name_key(name), y, team)) or by.get((cast_mod.name_key(name), y, None)) or set()
            if len(cands) == 1:
                pid = next(iter(cands))
                cur.execute(f"UPDATE {table} SET player_id = %s WHERE id = %s", (pid, row_id))
                linked.append((table, row_id, pid))
    return linked


def revert_players(conn_info=CONN_INFO):
    """Delete every SYNTH player row and the players the backfill created; restore the canon rows it touched."""
    with psycopg.connect(conn_info) as conn, conn.cursor() as cur:
        n = 0
        cur.execute("DELETE FROM awards WHERE source = 'SYNTH'")   # generated All-Pro (they point at these players)
        n += cur.rowcount
        cur.execute("SELECT id, report FROM history_player_runs WHERE applied_at IS NOT NULL")
        for run_id, report in cur.fetchall():
            for ch in (report.get("applied") or {}).get("canon_changes", []):
                cur.execute(f"UPDATE season_stats SET {', '.join(f'{c} = %s' for c in ch['old'])} WHERE id = %s",
                            (*ch["old"].values(), ch["id"]))
            for table, row_id, _pid in (report.get("applied") or {}).get("linked_facts", []):
                cur.execute(f"UPDATE {table} SET player_id = NULL WHERE id = %s", (row_id,))
            cur.execute("UPDATE history_player_runs SET applied_at = NULL WHERE id = %s", (run_id,))
        for t in PLAYER_TABLES:
            cur.execute(f"DELETE FROM {t} WHERE source = 'SYNTH'")
            n += cur.rowcount
        cur.execute("DELETE FROM players WHERE origin IN ('HISTORY', 'HISTORY_FILLIN')")
        n += cur.rowcount
        conn.commit()
    return n


def link_applied_facts(conn_info=CONN_INFO):
    """link_facts for the applied run (it predates the linking step), kept in its report for revert."""
    with psycopg.connect(conn_info) as conn, conn.cursor() as cur:
        cur.execute("SELECT id, report FROM history_player_runs WHERE applied_at IS NOT NULL")
        got = cur.fetchone()
        if not got:
            raise SystemExit("no applied run")
        run_id, report = got
        linked = link_facts(cur)
        report.setdefault("applied", {}).setdefault("linked_facts", []).extend(linked)
        cur.execute("UPDATE history_player_runs SET report = %s WHERE id = %s", (json.dumps(report, default=str), run_id))
        conn.commit()
    return len(linked)


# ------------------------------------------------------------------------------------------ records check ----

RECORDS_MD = os.path.join(ROOT, "verify_output", "history_records_check.md")


def records_check(conn_info=CONN_INFO, first=2026, last=2034):
    """The records engine (engines/records_engine.evaluate, read-only) over every history season, measured against
    the record book as it stood before each one. Lists franchise records broken or tied outside the book, NFL top-10
    entries outside the book (today's lists, and as they stood then), and book records the stats don't reproduce.
    Writes verify_output/history_records_check.md; returns the counts."""
    from engines import records_engine
    lines, counts = [], Counter()
    with psycopg.connect(conn_info) as conn, conn.cursor() as cur:
        cur.execute("SELECT category, value FROM league_record_entries")
        by_cat = defaultdict(list)
        for cat, v in cur.fetchall():
            by_cat[cat].append(float(v))
        tenth = {c: sorted(v, reverse=True)[9] for c, v in by_cat.items() if len(v) >= 10}
        for y in range(first, last + 1):
            r = records_engine.evaluate(cur, y)
            cur.execute("SELECT category, value FROM league_record_entries WHERE season = %s AND source = 'BOOK'", (y,))
            book = [(c, float(v)) for c, v in cur.fetchall()]
            in_book = lambda e: any(c == e["category"] and abs(v - float(e["value"])) < 0.01 for c, v in book)
            franchise = [e for e in r["events"] if e["type"] == "franchise_record" and not e.get("in_book")]
            top = [e for e in r["events"] if e["type"] == "top10" and not in_book(e)]
            today = [e for e in top if float(e["value"]) >= tenth.get(e["category"], float("inf"))]
            counts["franchise records outside the book"] += len(franchise)
            counts["top-10 today outside the book"] += len(today)
            counts["top-10 as they stood then (not today)"] += len(top) - len(today)
            counts["book records the stats miss"] += len(r["book_only"])
            for e in franchise:
                lines.append(f"- {y} franchise record {e['result']}: {e['team']}, {e['label']}, {e['holder']} "
                             f"{e['value']} (was {e['old_value']}, {', '.join(e['old_holders'][:2])})")
            for e in today:
                lines.append(f"- {y} NFL top 10 today: {e['label']}, {e['holder']} {e['value']} (rank {e['rank']})")
            for b in r["book_only"]:
                lines.append(f"- {y} book record the stats miss: {b['team']}, {b['label']}, {b['holder']} {b['value']}")
    md = ["# Records check, 2026–2034 history", "",
          "The records engine over each history season (read-only), against the record book as it stood before it.",
          "", *[f"- {k}: {v}" for k, v in counts.items()], "", "## Details", "", *(lines or ["- none"])]
    os.makedirs(os.path.dirname(RECORDS_MD), exist_ok=True)
    with open(RECORDS_MD, "w") as fh:
        fh.write("\n".join(md) + "\n")
    return dict(counts)


# ------------------------------------------------------------------------------------------ leader ranges ----

LEADERS_MD = os.path.join(ROOT, "verify_output", "history_leaders_report.md")
# (label, season_stat_lines expression, a season's leader is the max; "per team" totals are the league sum / 32)
LEADER_STATS = (("Passing yards", "pass_yards"), ("Passing TDs", "pass_tds"), ("Interceptions thrown", "interceptions"),
                ("Rushing yards", "rush_yards"), ("Rushing TDs", "rush_tds"), ("Receptions", "receptions"),
                ("Receiving yards", "rec_yards"), ("Receiving TDs", "rec_tds"), ("Sacks", "def_sacks"),
                ("Tackles", "def_tackles"), ("Interceptions", "def_ints"), ("Field goals made", "kick_fgm"),
                ("Punting average (40+ punts)", "punt_average"), ("Kick-return yards", "kr_yards"))
MILESTONES = (("4,000-yard passers", "pass_yards", 4000), ("1,000-yard rushers", "rush_yards", 1000),
              ("1,000-yard receivers", "rec_yards", 1000), ("10-sack seasons", "def_sacks", 10),
              ("100-tackle seasons", "def_tackles", 100))
CAPTURED = (2035, 2036)
SLACK = 0.10              # within 10% of the captured range counts as inside it


def range_flag(value, lo, hi, slack=SLACK):
    """'' inside [lo, hi] widened by slack, else 'below' / 'above' (pure)."""
    if value is None or lo is None:
        return ""
    if value < lo * (1 - slack):
        return "below"
    if value > hi * (1 + slack):
        return "above"
    return ""


def leaders_report(conn_info=CONN_INFO, first=2026, last=2034):
    """Each history season's leaders, top-10 averages, per-team totals and milestone counts against the captured
    2035-2036 range (verify_output/history_leaders_report.md). Returns {season: [flagged measures]}."""
    seasons = list(range(first, last + 1)) + list(CAPTURED)
    data = defaultdict(dict)
    with psycopg.connect(conn_info) as conn, conn.cursor() as cur:
        for label, col in LEADER_STATS:
            cond = "punt_punts >= 40" if col == "punt_average" else f"{col} IS NOT NULL"
            cur.execute(f"""SELECT season, array_agg({col} ORDER BY {col} DESC NULLS LAST), sum({col}) FROM season_stat_lines
                            WHERE season = ANY(%s) AND {cond} GROUP BY season""", (seasons,))
            for y, vals, total in cur.fetchall():
                vals = [float(v) for v in vals if v is not None]
                if not vals:
                    continue
                data[y][f"{label}: leader"] = vals[0]
                data[y][f"{label}: top-10 average"] = round(sum(vals[:10]) / min(10, len(vals)), 1)
                if col != "punt_average":
                    data[y][f"{label}: per team"] = round(float(total) / 32, 1)
        for label, col, th in MILESTONES:
            cur.execute(f"SELECT season, count(*) FROM season_stat_lines WHERE season = ANY(%s) AND {col} >= %s "
                        f"GROUP BY season", (seasons, th))
            for y, n in cur.fetchall():
                data[y][label] = n
    measures = sorted({k for y in data for k in data[y]}, key=lambda k: [m for m in
                      [f"{l}: {s}" for l, _ in LEADER_STATS for s in ("leader", "top-10 average", "per team")]
                      + [l for l, _, _ in MILESTONES]].index(k))
    flags, rows = defaultdict(list), []
    for m in measures:
        cap = [data[y][m] for y in CAPTURED if m in data[y]]
        lo, hi = (min(cap), max(cap)) if cap else (None, None)
        cells = []
        for y in range(first, last + 1):
            v = data[y].get(m)
            f = range_flag(v, lo, hi)
            if f:
                flags[y].append(f"{m} {v:g} ({f} {lo:g}–{hi:g})")
            cells.append("—" if v is None else f"{v:g}{' ↓' if f == 'below' else ' ↑' if f == 'above' else ''}")
        rows.append(f"| {m} | " + " | ".join(cells) + f" | {lo:g}–{hi:g} |" if lo is not None else
                    f"| {m} | " + " | ".join(cells) + " | — |")
    md = ["# Leader ranges, 2026–2034 history", "",
          f"Each history season against the captured {CAPTURED[0]}–{CAPTURED[1]} range (↓ / ↑: more than "
          f"{int(SLACK * 100)}% outside it). Leaders sit below the Madden era by design where the record caps hold "
          "them under today's NFL top 10, and 2026 starts from real-NFL levels.", "",
          f"- Measures outside the range: {sum(len(v) for v in flags.values())} of "
          f"{len(measures) * (last - first + 1)}", "",
          "| Measure | " + " | ".join(str(y) for y in range(first, last + 1)) + f" | {CAPTURED[0]}–{CAPTURED[1]} |",
          "|---|" + "---|" * (last - first + 2), *rows]
    os.makedirs(os.path.dirname(LEADERS_MD), exist_ok=True)
    with open(LEADERS_MD, "w") as fh:
        fh.write("\n".join(md) + "\n")
    return dict(flags)
