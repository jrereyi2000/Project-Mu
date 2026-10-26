"""
2026-2034 remainders: what each player's career totals leave for the seasons the backfill generates
(docs/HISTORY_BACKFILL_PLAN.md §3.1; PROJECT_SPEC §3.3i).

  python3 -m engines.history_backfill.remainder [--season S]      # S = the captured season, default the latest

For every player with career totals (player_career_totals, captured in season S) and every counting stat:

  mu       = career - sum of captured seasons 2035..S (season_stat_lines) - real NFL through 2025 (B6, K5)
  generate = mu - stored canon seasons 2026..2034 (season_stats, e.g. the Panthers' 2032-2034 lines)

`generate` is what phase 3 spreads over the player's open seasons: 2026 (or his first season, from
years_of_experience) through 2034. Flags:
- negative:          some stat's remainder is below zero (a misread, a wrong match or a wrong pre-2026 line)
- no_open_seasons:   a remainder left over but his first season is 2035 or later
- missing_pre2026:   he entered before 2026 but has no real-career row (nfl_careers_through_2025.csv)
- untracked_pre2026: he entered before 2026 and some stats nflverse doesn't carry (broken tackles, YAC, run after
                     catch, sacks allowed, ...) still hold his real pre-2026 share; those columns are listed per player
Players with a captured 2035..S line but no career row are listed separately: rostered in S (cut off a list, or
never listed) or no longer rostered (gone before the capture; the lists carry active players only). Madden's career
GP isn't comparable to season games played, so games are left out; a column with no career row (a category the
player isn't listed in) is unknown, not zero, and isn't subtracted.

Writes verify_output/history_remainders_<S>.csv (one row per player) and history_remainders_<S>.md (summary).
compute() is pure; the rest reads the database and data/history/nfl_careers_through_2025.csv. Writes nothing to the
database.
"""
import argparse
import csv
import os
from collections import Counter

import psycopg

from pipeline.load_career_totals import CATEGORIES, column_map

CONN_INFO = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")
ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
CAREERS_CSV = os.path.join(ROOT, "data", "history", "nfl_careers_through_2025.csv")
OUT_DIR = os.path.join(ROOT, "verify_output")
FIRST_MU, LAST_GENERATED, FIRST_CAPTURED = 2026, 2034, 2035
NOT_SUMMABLE = ("pct", "avg", "average", "ypg", "ypa", "rating", "long", "longest")
TOLERANCE = 0.05          # half-sacks and float reads


def summable_columns():
    cols = {col for cat in CATEGORIES for col, _ in column_map(cat).values()}
    return sorted(c for c in cols if not c.endswith(NOT_SUMMABLE))


COLUMNS = summable_columns()


def _add(into, line, sign=1, only=None):
    for c in only or COLUMNS:
        v = line.get(c)
        if v not in (None, ""):
            into[c] = into.get(c, 0) + sign * float(v)


def compute(career, captured, canon, pre2026, first_season, pre_columns):
    """career {pid: {col: v}}; captured / canon {pid: [line]}; pre2026 {pid: {col: v}}; first_season {pid: int};
    pre_columns: the columns the real-career file carries. Returns {pid: {mu, generate, open_seasons, flags,
    untracked}} (pure)."""
    out = {}
    for pid, tot in career.items():
        # only the columns his career rows carry: no row in a category (an offensive player's 2 tackles below a
        # team's defense list) means unknown, not zero
        cols = [c for c in COLUMNS if tot.get(c) is not None]
        mu = {}
        _add(mu, tot, only=cols)
        for line in captured.get(pid, []):
            _add(mu, line, -1, cols)
        first = first_season.get(pid)
        entered_before = first is not None and first < FIRST_MU
        if pid in pre2026:
            _add(mu, pre2026[pid], -1, cols)
        gen = dict(mu)
        for line in canon.get(pid, []):
            _add(gen, line, -1, cols)
        gen = {c: round(v, 1) for c, v in gen.items()}
        start = max(FIRST_MU, first) if first is not None else FIRST_MU
        open_seasons = max(0, LAST_GENERATED - start + 1)
        flags = []
        if any(v < -TOLERANCE for v in gen.values()):
            flags.append("negative")
        if open_seasons == 0 and any(v > TOLERANCE for v in gen.values()):
            flags.append("no_open_seasons")
        untracked = []
        if entered_before:
            if pid not in pre2026:
                flags.append("missing_pre2026")
            else:
                untracked = sorted(c for c, v in gen.items() if c not in pre_columns and v > TOLERANCE)
                if untracked:
                    flags.append("untracked_pre2026")
        out[pid] = {"mu": {c: round(v, 1) for c, v in mu.items()}, "generate": gen, "open_seasons": open_seasons,
                    "first_season": first, "flags": flags, "untracked": untracked}
    return out


# ---------- reads ----------

def latest_captured_season(cur):
    cur.execute("SELECT max(captured_season) FROM player_career_totals")
    return cur.fetchone()[0]


def read_career(cur, season):
    cur.execute("SELECT player_id, stats FROM player_career_totals WHERE captured_season = %s AND player_id IS NOT NULL",
                (season,))
    career = {}
    for pid, stats in cur.fetchall():
        t = career.setdefault(pid, {})
        for c, v in stats.items():
            if c != "games_played":      # Madden's career GP isn't comparable to season GP (JR's 2036 capture)
                t[c] = v
    return career


def read_lines(cur, sql, args, cols):
    cur.execute(sql.format(cols=", ".join(cols)), args)
    out = {}
    for row in cur.fetchall():
        out.setdefault(row[0], []).append({c: v for c, v in zip(cols, row[1:]) if v is not None})
    return out


def read_first_seasons(cur):
    cur.execute("SELECT DISTINCT ON (player_id) player_id, season - years_of_experience FROM player_seasons "
                "WHERE years_of_experience IS NOT NULL ORDER BY player_id, season DESC")
    return dict(cur.fetchall())


def read_pre2026(path=CAREERS_CSV):
    pre, cols = {}, set()
    if not os.path.exists(path):
        return pre, cols
    with open(path) as f:
        reader = csv.DictReader(f)
        cols = set(reader.fieldnames or ())
        for r in reader:
            if r.get("player_id"):
                pre[int(r["player_id"])] = r
    return pre, cols


def build(cur, season):
    career = read_career(cur, season)
    line_cols = [c for c in COLUMNS]
    captured = read_lines(cur, "SELECT player_id, {cols} FROM season_stat_lines WHERE season BETWEEN %s AND %s",
                          (FIRST_CAPTURED, season), line_cols)
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'season_stats'")
    have = {r[0] for r in cur.fetchall()}
    ss_cols = [c for c in COLUMNS if c in have]
    canon = read_lines(cur, "SELECT player_id, {cols} FROM season_stats WHERE season BETWEEN %s AND %s "
                            "AND player_id IS NOT NULL", (FIRST_MU, LAST_GENERATED), ss_cols)
    pre, pre_cols = read_pre2026()
    first = read_first_seasons(cur)
    result = compute(career, captured, canon, pre, first, pre_cols)
    # captured 2035..S lines with no career row: the list was cut off, or the row wasn't matched
    no_row = sorted(pid for pid, lines in captured.items() if pid not in career
                    and any(float(v) for line in lines for c, v in line.items() if v))
    cur.execute("SELECT player_id FROM player_seasons WHERE season = %s AND team_id IS NOT NULL", (season,))
    rostered = {r[0] for r in cur.fetchall()}
    return result, [p for p in no_row if p in rostered], [p for p in no_row if p not in rostered]


def write_report(cur, season, result, no_row, gone):
    os.makedirs(OUT_DIR, exist_ok=True)
    cur.execute("SELECT id, full_name, player_name FROM players")
    names = {i: f or p for i, f, p in cur.fetchall()}
    cur.execute("SELECT DISTINCT ON (player_id) player_id, position FROM player_seasons ORDER BY player_id, season DESC")
    pos = dict(cur.fetchall())
    csv_path = os.path.join(OUT_DIR, f"history_remainders_{season}.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["player_id", "name", "position", "first_season", "open_seasons", "flags", "untracked"] + COLUMNS)
        for pid in sorted(result, key=lambda p: names.get(p) or ""):
            r = result[pid]
            w.writerow([pid, names.get(pid), pos.get(pid), r["first_season"], r["open_seasons"], " ".join(r["flags"]),
                        " ".join(r["untracked"])] + [r["generate"].get(c, "") for c in COLUMNS])
    flags = Counter(f for r in result.values() for f in r["flags"])
    neg = [(pid, r) for pid, r in result.items() if "negative" in r["flags"]]
    lines = [f"# History backfill remainders, careers captured in {season}", "",
             f"- Players with career totals: {len(result)}",
             f"- With a 2026–2034 remainder to generate: "
             f"{sum(1 for r in result.values() if any(v > TOLERANCE for v in r['generate'].values()))}",
             f"- Flags: " + (", ".join(f"{k} {v}" for k, v in sorted(flags.items())) or "none"),
             f"- On a {season} roster with a captured line but no career row: {len(no_row)}",
             f"- Captured {FIRST_CAPTURED}–{season} lines of players no longer rostered (left before the capture, so "
             f"not on the active-player lists): {len(gone)}", "",
             "## Negative remainders (first 25)", ""]
    for pid, r in sorted(neg, key=lambda x: names.get(x[0]) or "")[:25]:
        bad = ", ".join(f"{c} {v:g}" for c, v in r["generate"].items() if v < -TOLERANCE)
        lines.append(f"- {names.get(pid)} ({pos.get(pid)}, id {pid}): {bad}")
    lines += ["", f"## On a {season} roster, no career row (first 40)", ""]
    lines += [f"- {names.get(pid)} ({pos.get(pid)}, id {pid})" for pid in no_row[:40]]
    md_path = os.path.join(OUT_DIR, f"history_remainders_{season}.md")
    with open(md_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return csv_path, md_path, flags


def main():
    ap = argparse.ArgumentParser(description="Report each player's 2026-2034 remainder from his career totals.")
    ap.add_argument("--season", type=int, help="captured season (default: the latest in player_career_totals)")
    a = ap.parse_args()
    with psycopg.connect(CONN_INFO) as conn, conn.cursor() as cur:
        season = a.season or latest_captured_season(cur)
        if season is None:
            raise SystemExit("player_career_totals is empty: capture, extract and load Career Stats first")
        result, no_row, gone = build(cur, season)
        csv_path, md_path, flags = write_report(cur, season, result, no_row, gone)
    print(f"{len(result)} players; flags {dict(flags)}; {len(no_row)} rostered players without a career row; "
          f"{len(gone)} no longer rostered")
    print(f"wrote {os.path.relpath(csv_path)} and {os.path.relpath(md_path)}")


if __name__ == "__main__":
    main()
