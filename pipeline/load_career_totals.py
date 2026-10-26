"""
Load Career Stats lists into player_career_totals (docs/HISTORY_BACKFILL_PLAN.md phase 1; PROJECT_SPEC §3.3i).

  python3 -m pipeline.load_career_totals captures/<run> [--dry-run] [--allow-missing]

Reads career_<category>.csv (extract.py --career, from madden_capture.py --only career), the same columns as the
Season Stats lists (JR, 2026-10-26), and writes one player_career_totals row per list row for league_state's season,
replacing that season's rows for each category it loads. Values use the season_stat_lines column names
(passing/rushing/receiving as in season_stats; defense def_*, kicking kick_*, punting punt_*, returns kr_* / pr_*,
blocking blk_*), plus games_played from GP.

League-wide lists stop at 250 rows, so the long categories are also captured team by team (madden_capture.py
--only career-teams -> career_scope_<team>_<category>.csv). A team's row is matched by list name key + position +
that team and wins over the league-wide row for the same player; the league-wide rows fill in everyone else.
Rows are matched like Season Stats rows: list name key + position against the season's rostered players
(load_capture.roster). Several players sharing both are settled by their season lines: a player's career total
can't be lower than his total for the season in any counting stat, and each player takes one row. A row that matches
nobody, or can't be settled, is stored with player_id NULL and a flag (unmatched / ambiguous); nothing is guessed.
A missing category CSV stops the load unless --allow-missing. One transaction; --dry-run rolls back.
"""
import argparse
import itertools
import json
import os
import sys

import psycopg

from pipeline.capture_matching import name_key, num
from pipeline.load_capture import (CATEGORY, DSN, SEASON_OFFENSE, LoadError, counters_of, misread_copies, parse_row,
                                   read_csv, roster, team_index)

# CATEGORY's column names -> season_stat_lines names
PREFIX = {"defense": "def_", "kicking": "kick_", "punting": "punt_", "kickreturn": "kr_", "puntreturn": "pr_",
          "blocking": "blk_"}
CATEGORIES = list(SEASON_OFFENSE) + list(CATEGORY)
MAX_SETTLE_GROUP = 5          # players sharing a list name + position settled by trying every assignment


def column_map(cat):
    """{list header: (season_stat_lines column, parser)} for one category."""
    if cat in SEASON_OFFENSE:
        return SEASON_OFFENSE[cat]
    return {h: (PREFIX[cat] + col, parse) for h, (col, parse) in CATEGORY[cat].items()}


def row_values(row, cmap, where):
    vals = parse_row(row, cmap, where)
    if row.get("GP", "") != "":
        try:
            vals["games_played"] = num(row["GP"])
        except ValueError as e:
            raise LoadError(f"{where}: GP {e}")
    return vals


def fits(career, season_line):
    """A career line can belong to a player only if no counting stat is below his season total."""
    if not season_line:
        return True
    for c in counters_of(list(career)):
        s = season_line.get(c)
        if s is not None and career.get(c) is not None and career[c] < s:
            return False
    return True


def settle(rows, cands, season_lines):
    """rows: [values] sharing a name key + position; cands: [player_id]. Returns {row index: player_id} when exactly
    one assignment (each player at most one row, every row a player that fits) works, else None (pure)."""
    if len(cands) > MAX_SETTLE_GROUP or len(rows) > len(cands):
        return None
    found = None
    for perm in itertools.permutations(cands, len(rows)):
        if all(fits(rows[i], season_lines.get(pid)) for i, pid in enumerate(perm)):
            if found is not None:
                return None
            found = dict(enumerate(perm))
    return found


def _sig(key, pos, vals):
    return key, pos, json.dumps(vals, sort_keys=True, default=float)


def match_rows(cat, rows, cmap, players, season_lines, scoped=None):
    """[(player_id or None, row, values, flags)] for one category (pure). scoped: {team_id: [row]} from the
    team-by-team lists, matched first by name key + position + team."""
    by_key, by_team = {}, {}
    for pid, key, team, pos, _pn in players:
        by_key.setdefault((key, pos), []).append(pid)
        by_team.setdefault((key, pos, team), []).append(pid)
    out, taken, resolved = [], set(), set()
    for tid, trows in sorted((scoped or {}).items()):
        for r in trows:
            key, pos = name_key(r["NAME"]), r["POS"]
            vals = row_values(r, cmap, f"{cat} {r['NAME']} (team {tid})")
            sig = _sig(key, pos, vals)
            if sig in resolved:
                continue                                   # the same row read twice on overlapping pages
            cands = [p for p in by_team.get((key, pos, tid), []) if p not in taken]
            if len(cands) == 1:
                out.append((cands[0], r, vals, ["team_list"]))
                taken.add(cands[0])
            else:
                out.append((None, r, vals, ["team_list", "unmatched" if not cands else "ambiguous"]))
            resolved.add(sig)
    groups = {}
    for r in rows:
        groups.setdefault((name_key(r["NAME"]), r["POS"]), []).append(r)
    for (key, pos), rs in groups.items():
        values = [row_values(r, cmap, f"{cat} {r['NAME']}") for r in rs]
        keep = [i for i, v in enumerate(values) if _sig(key, pos, v) not in resolved]
        rs, values = [rs[i] for i in keep], [values[i] for i in keep]
        if not rs:
            continue
        flags = []
        if len(rs) > 1 and misread_copies(rs, cmap):
            rs, values, flags = rs[:1], values[:1], ["misread_copy"]   # one row read twice on overlapping pages
        cands = [p for p in by_key.get((key, pos), []) if p not in taken]
        if not cands:
            out += [(None, r, v, flags + ["unmatched"]) for r, v in zip(rs, values)]
            continue
        if len(cands) == 1 and len(rs) == 1:
            ok = fits(values[0], season_lines.get(cands[0]))
            out.append((cands[0] if ok else None, rs[0], values[0], flags + ([] if ok else ["below_season_total"])))
            continue
        got = settle(values, cands, season_lines)
        if got is None:
            out += [(None, r, v, flags + ["ambiguous"]) for r, v in zip(rs, values)]
        else:
            out += [(got[i], r, v, flags) for i, (r, v) in enumerate(zip(rs, values))]
    return out


def season_lines_of(cur, season):
    """{player_id: {season_stat_lines column: value}} for the season (counting stats the career lists carry)."""
    cols = sorted({col for cat in CATEGORIES for col, _ in column_map(cat).values()})
    cur.execute(f"SELECT player_id, {', '.join(cols)} FROM season_stat_lines WHERE season = %s", (season,))
    return {r[0]: {c: v for c, v in zip(cols, r[1:]) if v is not None} for r in cur.fetchall()}


def scoped_career_rows(cur, folder):
    """career_scope_<team>_<category>.csv -> {category: {team_id: [row]}}."""
    import glob
    by_name, _ = team_index(cur)
    out = {}
    for path in glob.glob(os.path.join(folder, "career_scope_*.csv")):
        team, cat = os.path.basename(path)[len("career_scope_"):-4].split("_", 1)
        tid = by_name.get(team.upper())
        if tid is None:
            raise LoadError(f"{os.path.basename(path)}: no team named {team}")
        out.setdefault(cat, {})[tid] = read_csv(path)
    return out


def load(folder, dry_run=False, allow_missing=False):
    folder = folder.rstrip("/")
    report = {"folder": folder, "dry_run": dry_run, "categories": {}}
    with psycopg.connect(DSN) as conn:
        cur = conn.cursor()
        cur.execute("SELECT current_season, current_week FROM league_state WHERE id = 1")
        season, week = cur.fetchone()
        report["season"], report["week"] = season, week
        players = roster(cur, season)
        season_lines = season_lines_of(cur, season)
        scoped = scoped_career_rows(cur, folder)
        report["team_lists"] = {cat: len(t) for cat, t in scoped.items()}
        problems, loaded = [], {}
        for cat in CATEGORIES:
            path = os.path.join(folder, f"career_{cat}.csv")
            if not os.path.exists(path):
                (report.setdefault("missing", []) if allow_missing else problems).append(f"career_{cat}.csv is missing")
                continue
            try:
                loaded[cat] = match_rows(cat, read_csv(path), column_map(cat), players, season_lines, scoped.get(cat))
            except LoadError as e:
                problems.append(str(e))
        if problems:
            raise LoadError("career checks failed:\n  " + "\n  ".join(problems))

        # a player with a season line in a category but no career row there: the list was cut off at the bottom
        for cat, got in loaded.items():
            have = {pid for pid, *_ in got if pid}
            cols = [c for c, _ in column_map(cat).values()]
            missing = [pid for pid, line in season_lines.items()
                       if pid not in have and any(line.get(c) for c in counters_of(cols))]
            flagged = {}
            for _, _, _, fl in got:
                for f in fl:
                    flagged[f] = flagged.get(f, 0) + 1
            report["categories"][cat] = {"rows": len(got), "matched": len(have), "flags": flagged,
                                         "season_players_without_career_row": len(missing)}
            if missing and cat not in scoped:
                report.setdefault("warnings", []).append(
                    f"{cat}: {len(missing)} players with a {season} line have no career row (list cut off at 250 "
                    f"rows?); capture it team by team: madden_capture.py --only career-teams --category {cat.upper()}")

        for cat, got in loaded.items():
            cur.execute("DELETE FROM player_career_totals WHERE captured_season = %s AND category = %s", (season, cat))
            for pid, r, vals, flags in got:
                cur.execute("INSERT INTO player_career_totals (captured_season, captured_week, capture_folder, category, "
                            "player_id, list_name, position, stats, flags) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                            (season, week, os.path.basename(folder), cat, pid, r["NAME"], r["POS"],
                             json.dumps(vals, default=float), flags))
        if dry_run:
            conn.rollback()
        else:
            conn.commit()
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder")
    ap.add_argument("--dry-run", action="store_true", help="run the checks and report, write nothing")
    ap.add_argument("--allow-missing", action="store_true", help="load the categories that are there")
    a = ap.parse_args()
    try:
        report = load(a.folder, a.dry_run, a.allow_missing)
    except LoadError as e:
        print(f"LOAD STOPPED, nothing written: {e}")
        sys.exit(1)
    print(json.dumps(report, indent=1, default=str))


if __name__ == "__main__":
    main()
