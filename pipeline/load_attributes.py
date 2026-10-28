"""
Loads a PS5 attribute scan (docs/PC_MIGRATION_PLAN.md §2.3) into player_attributes, in one transaction.

  python3 -m pipeline.attribute_reader RUN                  # captures/RUN/attributes/ -> attributes.csv
  python3 -m pipeline.load_attributes RUN [--season N] [--commit]

Without --commit the load runs and reports, then rolls back. --season defaults to league_state.current_season.
Reads captures/RUN/attributes/attributes.csv (running pipeline/attribute_reader.py first when it is missing).

Matching: each scan row is matched to a player_seasons row of the same team and season, one to one:
  1. list-name key ('T.DAVIS' -> TDAVIS, capture_matching.name_key) and position; a key shared by two players is
     decided by position, then age;
  2. one OCR slip in the name (capture_matching.one_slip) with the same position;
  3. otherwise the row is reported unmatched and not loaded.
A team's rows already loaded from the same capture folder are replaced; other scans are kept (one row per player
per scan, so the teleporter takes the latest of the season). With --commit the run gets a `captures` row (mode
`attributes`, status loaded).
"""
import argparse
import csv
import json
import os
import sys

import psycopg

from pipeline.capture_matching import name_key, one_slip

DSN = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
META = ("team", "row", "name", "pos", "age", "ovr", "predicted_ovr", "ovr_check", "confirmed", "flags")


def num(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def read_csv(path):
    """attributes.csv -> [row dict] with ints where they belong and `ratings` {field: value} (blank cells left out)."""
    rows = []
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            out = {k: r.get(k) for k in META}
            for k in ("row", "age", "ovr", "predicted_ovr", "confirmed"):
                out[k] = num(r.get(k))
            out["ratings"] = {k: int(v) for k, v in r.items() if k not in META and v not in (None, "")}
            rows.append(out)
    return rows


def match_rows(rows, candidates):
    """rows: one team's scan rows; candidates: [{player_id, name, position, age}] for that team and season.
    Returns ([(row, player_id, how)], [unmatched row]). One to one: a player is matched at most once."""
    taken, matched, left = set(), [], []
    keyed = [(c, name_key(c["name"])) for c in candidates]

    def pick(pool, row):
        pool = [c for c in pool if c["player_id"] not in taken]
        if len(pool) > 1:
            same_pos = [c for c in pool if (c["position"] or "") == (row["pos"] or "")]
            pool = same_pos or pool
        if len(pool) > 1 and row["age"] is not None:
            same_age = [c for c in pool if c["age"] == row["age"]]
            pool = same_age or pool
        return pool[0] if len(pool) == 1 else None

    for row in rows:
        key = name_key(row["name"] or "")
        c = pick([c for c, k in keyed if k == key], row)
        how = "name"
        if c is None:
            c = pick([c for c, k in keyed if key and one_slip(k, key) and (c["position"] or "") == (row["pos"] or "")],
                     row)
            how = "one slip"
        if c is None:
            left.append(row)
            continue
        taken.add(c["player_id"])
        matched.append((row, c["player_id"], how))
    return matched, left


def team_ids(cur):
    cur.execute("SELECT id, name FROM teams")
    return {name.upper(): tid for tid, name in cur.fetchall()}


def candidates(cur, season, team_id):
    cur.execute("""SELECT ps.player_id, p.player_name, ps.position, ps.age
                   FROM player_seasons ps JOIN players p ON p.id = ps.player_id
                   WHERE ps.season = %s AND ps.team_id = %s""", (season, team_id))
    return [dict(zip(("player_id", "name", "position", "age"), r)) for r in cur.fetchall()]


def load(cur, folder, rows, season, log=print):
    """Write matched rows; returns {"loaded", "unmatched", "by_one_slip", "ovr_ok", "teams"}."""
    teams = team_ids(cur)
    by_team = {}
    for r in rows:
        by_team.setdefault((r["team"] or "").upper(), []).append(r)
    totals = {"loaded": 0, "unmatched": 0, "by_one_slip": 0, "ovr_ok": 0, "teams": 0}
    for team, trows in sorted(by_team.items()):
        tid = teams.get(team)
        if tid is None:
            log(f"  {team}: no such team, {len(trows)} rows skipped")
            totals["unmatched"] += len(trows)
            continue
        matched, left = match_rows(trows, candidates(cur, season, tid))
        cur.execute("DELETE FROM player_attributes WHERE capture_folder = %s AND team_id = %s", (folder, tid))
        for row, pid, how in matched:
            cur.execute("""INSERT INTO player_attributes (player_id, season, team_id, capture_folder, position, age,
                                                          overall_rating, predicted_ovr, ovr_check, confirmed, ratings,
                                                          flags)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                           ON CONFLICT (player_id, capture_folder) DO UPDATE SET
                             season = EXCLUDED.season, team_id = EXCLUDED.team_id, position = EXCLUDED.position,
                             age = EXCLUDED.age, overall_rating = EXCLUDED.overall_rating,
                             predicted_ovr = EXCLUDED.predicted_ovr, ovr_check = EXCLUDED.ovr_check,
                             confirmed = EXCLUDED.confirmed, ratings = EXCLUDED.ratings, flags = EXCLUDED.flags""",
                        (pid, season, tid, folder, row["pos"], row["age"], row["ovr"], row["predicted_ovr"],
                         row["ovr_check"], row["confirmed"], json.dumps(row["ratings"]), row["flags"] or None))
            totals["by_one_slip"] += how == "one slip"
            totals["ovr_ok"] += row["ovr_check"] == "ok"
        for row in left:
            log(f"  {team}: unmatched row {row['row']} {row['name']} {row['pos']} age {row['age']} OVR {row['ovr']}")
        totals["loaded"] += len(matched)
        totals["unmatched"] += len(left)
        totals["teams"] += 1
        log(f"{team}: {len(matched)} loaded, {len(left)} unmatched")
    return totals


def main():
    ap = argparse.ArgumentParser(description="Load a PS5 attribute scan into player_attributes.")
    ap.add_argument("run", help="capture run (captures/RUN) or its path")
    ap.add_argument("--season", type=int, help="default: league_state.current_season")
    ap.add_argument("--csv", help="default: <run>/attributes/attributes.csv")
    ap.add_argument("--commit", action="store_true", help="keep the load (default: report and roll back)")
    a = ap.parse_args()
    run_dir = a.run if os.path.isdir(a.run) else os.path.join(ROOT, "captures", a.run)
    folder = os.path.basename(os.path.normpath(run_dir))
    path = a.csv or os.path.join(run_dir, "attributes", "attributes.csv")
    if not os.path.exists(path):
        from pipeline import attribute_reader
        attribute_reader.read_run(run_dir, out=path)
    rows = read_csv(path)
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        season = a.season
        if season is None:
            cur.execute("SELECT current_season FROM league_state WHERE id = 1")
            season = cur.fetchone()[0]
        t = load(cur, folder, rows, season)
        print(f"{folder} season {season}: {t['loaded']} players loaded for {t['teams']} teams "
              f"({t['by_one_slip']} by a one-letter name slip), {t['unmatched']} unmatched, OVR check ok {t['ovr_ok']}")
        if a.commit:
            cur.execute("""INSERT INTO captures (folder, mode, season, status, row_counts, loaded_at)
                           VALUES (%s, 'attributes', %s, 'loaded', %s, now())
                           ON CONFLICT (folder) DO UPDATE SET status = 'loaded', row_counts = EXCLUDED.row_counts,
                                                              loaded_at = now()
                           WHERE captures.mode = 'attributes'""",
                        (folder, season, json.dumps({"attributes.csv": len(rows), "loaded": t["loaded"]})))
            conn.commit()
            print("Committed.")
        else:
            conn.rollback()
            print("Rolled back (dry run; add --commit to keep it).")


if __name__ == "__main__":
    sys.path.insert(0, ROOT)
    main()
