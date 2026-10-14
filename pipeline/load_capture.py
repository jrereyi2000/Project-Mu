"""
Load an extracted capture folder into the database (docs/PIPELINE_MIGRATION.md, phase 3).

  python3 -m pipeline.load_capture captures/<run> [--dry-run]

The mode is read from the folder: roster/ (roster), season_schedule/ (season schedule), games/
(games, recovery only), else stats/ + team_stats/ + schedule/ (weekly). One transaction per run:
any failed check writes nothing, records the run as failed in `captures` and exits non-zero.
--dry-run runs every check and prints what would change, then rolls back.
Uses DATABASE_URL (default: the local madden database); point it at the restored test copy
(dbname=madden_p3test) until a mode has passed there.
"""
import argparse, csv, json, os, sys
from datetime import datetime

import psycopg

from pipeline.capture_matching import db_player_name, dec, full_key, match_roster, name_case, name_key, num

DSN = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")


class LoadError(Exception):
    """A check failed: nothing is written."""


def read_csv(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def detect_mode(folder):
    if os.path.exists(os.path.join(folder, "roster_cards.csv")):
        return "roster"
    if os.path.exists(os.path.join(folder, "season_schedule.csv")):
        return "season_schedule"
    if os.path.exists(os.path.join(folder, "game_scores.csv")):
        return "games"
    if os.path.exists(os.path.join(folder, "stats_passing.csv")):
        return "weekly"
    raise LoadError(f"{folder}: no extracted CSVs found (run extract.py first)")


# ------------------------------------------------------------------ roster ----
ROSTER_TEAMS_MIN, ROSTER_TEAMS_MAX = 45, 56     # players per team seen on Oct 13 (52-56)


def load_roster(cur, folder, season, report):
    """roster_cards.csv (+ roster_list.csv for the list OVR) -> players, player_seasons."""
    cards = read_csv(os.path.join(folder, "roster_cards.csv"))
    lists = read_csv(os.path.join(folder, "roster_list.csv"))
    cur.execute("SELECT id, upper(name) FROM teams")
    team_ids = {n: i for i, n in cur.fetchall()}

    # list rows by (team, list name key, position); a repeated key keeps its rows in order
    by_list = {}
    for r in lists:
        by_list.setdefault((r["TEAM"], name_key(r["NAME"]), r["POS"]), []).append(r)

    problems, per_team = [], {}
    parsed = []
    for c in cards:
        team = c["team"]
        if team not in team_ids:
            problems.append(f"unknown team {team!r}")
            continue
        per_team[team] = per_team.get(team, 0) + 1
        first, last = name_case(c["first"].strip()), name_case(c["last"].strip())
        if not last:
            problems.append(f"{team} row {c['row']}: card has no last name ({c['card_png']})")
            continue
        try:
            number, age, weight, exp = num(c["number"]), num(c["age"]), num(c["weight"]), num(c["experience"])
        except ValueError as e:
            problems.append(f"{team} row {c['row']} {first} {last}: {e}")
            continue
        pos = c["position"].strip()
        key = name_key(f"{first[:1]}.{last}") if first else None
        rows = by_list.get((team, key, pos), []) if key and pos else []
        if not rows and key:                 # card position unread: the initial + last name on the team
            rows = [r for (t, k, p), rs in by_list.items() for r in rs if t == team and k == key]
        if not rows:                         # first name unread too: the last name on the team
            rows = [r for (t, k, p), rs in by_list.items() for r in rs
                    if t == team and k[1:] == full_key(last) and (not pos or p == pos)]
        if len(rows) != 1:
            problems.append(f"{team} row {c['row']} {first} {last} {pos}: {len(rows)} list rows match")
            continue
        lr = rows[0]
        pos = pos or lr["POS"]
        try:
            ovr = num(lr["OVR"])
        except ValueError as e:
            problems.append(f"{team} {lr['NAME']}: OVR {e}")
            continue
        if not first:
            problems.append(f"{team} row {c['row']} {last}: card has no first name ({c['card_png']})")
            continue
        parsed.append(dict(team=team, team_id=team_ids[team], first=first, last=last, number=number, position=pos,
                           age=age, height=c["height"].strip() or None, weight=weight, experience=exp,
                           dev=c["dev"] or None, archetype=c["archetype"] or None, role=c["role"] or None, ovr=ovr,
                           full_key=full_key(f"{first} {last}"), name_key=name_key(f"{first[:1]}.{last}")))
    for team, n in sorted(per_team.items()):
        if not ROSTER_TEAMS_MIN <= n <= ROSTER_TEAMS_MAX:
            problems.append(f"{team}: {n} players (expected {ROSTER_TEAMS_MIN}-{ROSTER_TEAMS_MAX})")
    if len(per_team) != 32:
        problems.append(f"{len(per_team)} teams in the capture, not 32")
    if problems:
        raise LoadError("roster checks failed:\n  " + "\n  ".join(problems))

    # existing players and their roster entry for the season
    cur.execute("""SELECT p.id, p.player_name, p.full_name, p.player_number, s.team_id, s.position
                   FROM players p LEFT JOIN player_seasons s ON s.player_id = p.id AND s.season = %s""", (season,))
    existing = [dict(id=i, name_key=name_key(pn), full_key=full_key(fn), number=no, team_id=t, position=po)
                for i, pn, fn, no, t, po in cur.fetchall()]
    by_full, by_name = {}, {}
    for e in existing:
        by_full.setdefault(e["full_key"], []).append(e)
        by_name.setdefault(e["name_key"], []).append(e)

    m, new_idx, ambiguous = match_roster(parsed, existing)
    if ambiguous:
        raise LoadError("roster: players that can't be told apart (nothing written):\n  " + "\n  ".join(
            f"{a} ({parsed[int(a.split()[1].rstrip(':'))]['team']} {parsed[int(a.split()[1].rstrip(':'))]['first']} "
            f"{parsed[int(a.split()[1].rstrip(':'))]['last']})" for a in ambiguous))
    matched = [(pid, how, parsed[i]) for i, (pid, how) in m.items()]
    new = [parsed[i] for i in new_idx]
    used = {pid: f"{p['team']} {p['first']} {p['last']}" for pid, _, p in matched}

    hows = {}
    for _, how, _ in matched:
        hows[how] = hows.get(how, 0) + 1
    report["matched"] = hows
    report["new_players"] = len(new)

    def write(pid, p):
        cur.execute("""UPDATE players SET player_name = %s, full_name = %s, height = %s, weight = %s, player_number = %s
                       WHERE id = %s""",
                    (db_player_name(p["first"], p["last"]), f"{p['first']} {p['last']}", p["height"], p["weight"],
                     p["number"], pid))
        cur.execute("""INSERT INTO player_seasons (player_id, season, team_id, position, age, overall_rating,
                           years_of_experience, dev_trait, archetype, role)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (player_id, season) DO UPDATE SET team_id = EXCLUDED.team_id,
                           position = EXCLUDED.position, age = EXCLUDED.age, overall_rating = EXCLUDED.overall_rating,
                           years_of_experience = EXCLUDED.years_of_experience, dev_trait = EXCLUDED.dev_trait,
                           archetype = EXCLUDED.archetype, role = EXCLUDED.role""",
                    (pid, season, p["team_id"], p["position"], p["age"], p["ovr"], p["experience"], p["dev"],
                     p["archetype"], p["role"]))

    changed_team = 0
    for pid, how, p in matched:
        old = next(e for e in existing if e["id"] == pid)
        if old["team_id"] is not None and old["team_id"] != p["team_id"]:
            changed_team += 1
        write(pid, p)
    for p in new:
        cur.execute("INSERT INTO players (player_name) VALUES (%s) RETURNING id", (db_player_name(p["first"], p["last"]),))
        write(cur.fetchone()[0], p)
    report["team_changes"] = changed_team

    seen = set(used)
    missing = [e for e in existing if e["team_id"] is not None and e["id"] not in seen]
    report["not_on_any_roster"] = len(missing)
    report["not_on_any_roster_sample"] = [e["id"] for e in missing[:20]]
    # a full 32-team capture is the whole league: anyone it didn't find is on no team this season
    # (JR, Oct 14: clear the team, delete nothing; stats and history stay)
    if missing and len(per_team) == 32:
        cur.execute("UPDATE player_seasons SET team_id = NULL WHERE season = %s AND player_id = ANY(%s)",
                    (season, [e["id"] for e in missing]))
        report["team_cleared"] = len(missing)
    return len(parsed)


# -------------------------------------------------------------------- runs ----
def capture_row(cur, folder, mode, season, week, dry_run=False):
    """The run's `captures` row (created or reset to extracted); a run already loaded is refused.
    A dry run only checks and returns None."""
    name = os.path.basename(os.path.normpath(folder))
    cur.execute("SELECT id, status FROM captures WHERE folder = %s", (name,))
    row = cur.fetchone()
    if row and row[1] == "loaded":
        raise LoadError(f"{name} is already loaded")
    if dry_run:
        return None
    if row:
        cur.execute("UPDATE captures SET mode = %s, season = %s, week = %s, status = 'extracted', error = NULL "
                    "WHERE id = %s", (mode, season, week, row[0]))
        return row[0]
    cur.execute("INSERT INTO captures (folder, mode, season, week, status, extracted_at) "
                "VALUES (%s, %s, %s, %s, 'extracted', now()) RETURNING id", (name, mode, season, week))
    return cur.fetchone()[0]


def row_counts(folder):
    out = {}
    for f in sorted(os.listdir(folder)):
        if f.endswith(".csv"):
            with open(os.path.join(folder, f)) as fh:
                out[f] = max(0, sum(1 for _ in fh) - 1)
    return out


LOADERS = {"roster": load_roster}


def load(folder, dry_run=False):
    folder = folder.rstrip("/")
    mode = detect_mode(folder)
    if mode not in LOADERS:
        raise LoadError(f"mode {mode} isn't built yet")
    report = {"folder": folder, "mode": mode}
    with psycopg.connect(DSN) as conn:
        cur = conn.cursor()
        cur.execute("SELECT current_season, current_week FROM league_state WHERE id = 1")
        season, week = cur.fetchone()
        report["season"] = season
        cid = capture_row(cur, folder, mode, season, None if mode == "roster" else week, dry_run)
        conn.commit()                        # the run is recorded even if the load fails
        try:
            report["rows"] = LOADERS[mode](cur, folder, season, report)
        except Exception as e:
            conn.rollback()
            if cid is not None:
                cur.execute("UPDATE captures SET status = 'failed', error = %s WHERE id = %s", (str(e)[:4000], cid))
                conn.commit()
            raise
        if dry_run:
            conn.rollback()
            report["dry_run"] = True
            return report
        cur.execute("UPDATE captures SET status = 'loaded', loaded_at = now(), row_counts = %s, error = NULL "
                    "WHERE id = %s", (json.dumps(row_counts(folder)), cid))
        conn.commit()
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder")
    ap.add_argument("--dry-run", action="store_true", help="run the checks and report, write nothing")
    a = ap.parse_args()
    try:
        report = load(a.folder, a.dry_run)
    except LoadError as e:
        print(f"LOAD STOPPED, nothing written: {e}")
        sys.exit(1)
    print(json.dumps(report, indent=1, default=str))


if __name__ == "__main__":
    main()
